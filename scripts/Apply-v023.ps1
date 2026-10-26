[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProjectPath,
    [switch]$Plan,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if ($Plan -and $Apply) { throw 'Choose only one of -Plan or -Apply' }
$project=(Resolve-Path -LiteralPath $ProjectPath).Path
$source=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$manifest=Get-Content -LiteralPath (Join-Path $source 'manifest-v023.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.target_version -ne '0.23.0' -or $manifest.base_version -ne '0.22.0') { throw 'Wrong release manifest' }
if (!(Test-Path -LiteralPath (Join-Path $project 'compose.yaml') -PathType Leaf)) { throw 'Not an autocodex project: compose.yaml missing' }
$patchEntries=@()
foreach ($entry in @($manifest.files)) {
    $rel=[string]$entry.path
    if ($rel -notmatch '^(app|static|templates|scripts|tests|docs|migrations/versions)/[A-Za-z0-9_./-]+$' -or $rel -match '(^|/)\.\.(/|$)' -or $rel -match '//') { throw "Unsafe patch path: $rel" }
    $src=Join-Path $source ($rel.Replace('/','\'))
    $dst=Join-Path $project ($rel.Replace('/','\'))
    if (!(Test-Path -LiteralPath $src -PathType Leaf)) { throw "Missing release file $rel" }
    $h=(Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($h -ne ([string]$entry.sha256).ToLowerInvariant()) { throw "Patch corrupted: $rel" }
    $existed=Test-Path -LiteralPath $dst -PathType Leaf
    if ($existed) {
        $actual=(Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $h -and $actual -ne ([string]$entry.baseline_sha256).ToLowerInvariant()) {
            throw "LOCAL FILE MODIFIED: $rel. The installer refuses to overwrite user modifications."
        }
    } elseif ([string]$entry.baseline_sha256) {
        throw "Expected existing v0.22 file missing: $rel"
    }
    $patchEntries += [pscustomobject]@{Rel=$rel;Source=$src;Destination=$dst;WasPresent=$existed;ExpectedSHA=$h}
}
$mainText=Get-Content -LiteralPath (Join-Path $project 'app\main.py') -Raw -Encoding UTF8
if ($mainText -notmatch 'version="0[.]22[.]0"' -and $mainText -notmatch 'version="0[.]23[.]0"') { throw 'Unexpected FastAPI version: requires v0.22.0' }
Push-Location $project
try {
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose configuration invalid' }
    $revision=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notin @('20261019_0008','20261020_0009')) { throw "Unexpected Alembic revision: $revision" }
    Write-Host "PREFLIGHT PASS: $($patchEntries.Count) verified files, Alembic $revision; no changes yet."
    if (!$Apply) {
        Write-Host 'PLAN ONLY: to install, rerun with -Apply after reviewing this output.'
        return
    }
    if ($revision -eq '20261020_0009') { throw 'v0.23 migration already applied. Stop and inspect before reinstalling.' }
    $stamp=Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup=Join-Path (Join-Path $HOME 'autoparts-backups') ("autocodex_v023_"+$stamp+'_'+[Guid]::NewGuid().ToString('N').Substring(0,6))
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Write-Host "Backing up source files to $backup"
    foreach ($e in $patchEntries) {
        if ($e.WasPresent) {
            $to=Join-Path $backup ($e.Rel.Replace('/','\'))
            New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force | Out-Null
            Copy-Item -LiteralPath $e.Destination -Destination $to -Force
        }
    }
    $remoteDump="/tmp/autocodex_before_v023_$stamp.dump"
    docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f $remoteDump
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL pg_dump failed. No source modifications made.' }
    docker compose exec -T db pg_restore --list $remoteDump | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL pg_restore --list failed. No source modifications made.' }
    $cid=((docker compose ps -q db) | Out-String).Trim()
    if (!$cid) { throw 'Database container ID unavailable' }
    $localDump=Join-Path $backup 'postgres_before_v023.dump'
    docker cp "${cid}:$remoteDump" $localDump
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $localDump -PathType Leaf) -or (Get-Item -LiteralPath $localDump).Length -lt 1024) { throw 'Cannot verify local PostgreSQL backup. Source unchanged.' }
    Write-Host "DATABASE BACKUP PASS: $localDump"
    foreach ($e in $patchEntries) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $e.Destination) -Force | Out-Null
        Copy-Item -LiteralPath $e.Source -Destination $e.Destination -Force
        if ((Get-FileHash -LiteralPath $e.Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $e.ExpectedSHA) { throw "Patch write failed: $($e.Rel)" }
    }
    Write-Host 'Starting migration and web (worker unchanged; no supplier action requested)...'
    docker compose up -d --build migrate web
    if ($LASTEXITCODE -ne 0) { throw 'Compose build/migration/start failed. Use backups; database may already be upgraded.' }
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v023
    if ($LASTEXITCODE -ne 0) { throw 'v0.23 smoke failed. Do not repeat the installer; inspect log and backup.' }
    Write-Host "INSTALL SUCCESS: Autoparts Product Workspace v0.23.0; backups: $backup"
    Write-Host 'No .env, PostgreSQL/media volumes, worker code, supplier credentials, purchases or payments were changed by this installer.'
} finally { Pop-Location }
