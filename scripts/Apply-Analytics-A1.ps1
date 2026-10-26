[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProjectPath,
    [switch]$Plan,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if (($Plan -and $Apply) -or (!$Plan -and !$Apply)) { throw 'Specify exactly one of -Plan or -Apply.' }
$project=(Resolve-Path -LiteralPath $ProjectPath).Path
$source=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$manifest=Get-Content -LiteralPath (Join-Path $source 'manifest-analytics-a1.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.base_version -ne '0.24.4-finance-f5-smoke-recovery' -or $manifest.target_version -ne '0.25.0-analytics-a1') { throw 'Unexpected release manifest' }
if (!(Test-Path -LiteralPath (Join-Path $project 'compose.yaml') -PathType Leaf)) { throw 'compose.yaml missing' }
$entries=@()
foreach ($item in @($manifest.files)) {
    $rel=[string]$item.path
    if ($rel -notmatch '^(app|static|templates|scripts|tests)/[A-Za-z0-9_./-]+$' -or $rel -match '(^|/)[.][.](/|$)' -or $rel -match '//') { throw "Unsafe patch path: $rel" }
    $src=Join-Path $source ($rel.Replace('/','\'))
    $dst=Join-Path $project ($rel.Replace('/','\'))
    if (!(Test-Path -LiteralPath $src -PathType Leaf)) { throw "Patch missing: $rel" }
    $hash=(Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne ([string]$item.sha256).ToLowerInvariant()) { throw "Patch hash mismatch: $rel" }
    $exists=Test-Path -LiteralPath $dst -PathType Leaf
    $baseline=[string]$item.baseline_sha256
    if ($exists) {
        if (!$baseline -or (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $baseline.ToLowerInvariant()) { throw "Unexpected local edits in $rel. Refusing overwrite." }
    } elseif ($baseline) { throw "Required baseline missing: $rel" }
    $entries += [pscustomobject]@{Rel=$rel;Src=$src;Dst=$dst;Existed=$exists;SHA=$hash}
}
$main=Get-Content -LiteralPath (Join-Path $project 'app\main.py') -Raw -Encoding UTF8
if ($main -notmatch 'version="0[.]24[.]4"') { throw 'Expected installed F5 FastAPI v0.24.4' }
$hotfix=Join-Path $project 'scripts\smoke_finance_f5.py'
if (!(Test-Path -LiteralPath $hotfix)) { throw 'Missing F5 recovery smoke file' }
if ((Get-FileHash -LiteralPath $hotfix -Algorithm SHA256).Hash.ToLowerInvariant() -ne ([string]$manifest.recovery_smoke_sha256)) { throw 'Expected F5 smoke recovery R1 not installed; refuse to proceed' }
Push-Location $project
try {
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Docker Compose configuration' }
    $revision=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -ne '20261020_0016') { throw "Unexpected Alembic revision: $revision" }
    Write-Host "ANALYTICS A1 PREFLIGHT PASS: $($entries.Count) files; Alembic $revision; no changes yet."
    if (!$Apply) { Write-Host 'PLAN ONLY: No source, database, container or business changes. Use -Apply after review.'; return }
    $stamp=Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup=Join-Path (Join-Path $HOME 'autoparts-backups') ('autocodex_analytics_a1_'+$stamp+'_'+[Guid]::NewGuid().ToString('N').Substring(0,6))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    foreach ($entry in $entries) {
        if ($entry.Existed) {
            $target=Join-Path $backup ($entry.Rel.Replace('/','\'))
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $entry.Dst -Destination $target -Force
        }
    }
    Write-Host "SOURCE BACKUP PASS: $backup"
    $remote="/tmp/autocodex_before_analytics_a1_$stamp.dump"
    docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f $remote
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed. No code changed.' }
    docker compose exec -T db pg_restore --list $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore list failed. No code changed.' }
    $cid=((docker compose ps -q db) | Out-String).Trim()
    if (!$cid) { throw 'Database container missing' }
    $local=Join-Path $backup 'postgres_before_analytics_a1.dump'
    docker cp "${cid}:${remote}" $local
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $local -PathType Leaf) -or (Get-Item -LiteralPath $local).Length -lt 1024) { throw 'Database backup copy failed. No code changed.' }
    Write-Host "DATABASE BACKUP PASS: $local"
    foreach ($entry in $entries) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $entry.Dst) | Out-Null
        Copy-Item -LiteralPath $entry.Src -Destination $entry.Dst -Force
        if ((Get-FileHash -LiteralPath $entry.Dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.SHA) { throw "Patch copy verification failed: $($entry.Rel)" }
    }
    Write-Host 'Rebuilding WEB ONLY. No migrations or worker/supplier/payment changes.'
    docker compose up -d --build web
    if ($LASTEXITCODE -ne 0) { throw 'Docker web rebuild failed. Do not rerun; inspect logs.' }
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_analytics_a1
    if ($LASTEXITCODE -ne 0) { throw 'A1 smoke failed. Do NOT reinstall; inspect log and backup.' }
    Write-Host "ANALYTICS A1 INSTALL SUCCESS: v0.25.0; Alembic $revision (unchanged); Backups: $backup"
    Write-Host 'READ ONLY dashboard. Operational data is UNVERIFIED, F3/F4/F5 shadow data excluded from finance KPIs.'
} finally { Pop-Location }
