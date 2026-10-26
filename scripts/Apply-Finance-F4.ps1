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
$manifest=Get-Content -LiteralPath (Join-Path $source 'manifest-finance-f4.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.base_version -ne '0.24.2-finance-f3' -or $manifest.target_version -ne '0.24.3-finance-f4') { throw 'Unexpected release manifest.' }
if (!(Test-Path -LiteralPath (Join-Path $project 'compose.yaml') -PathType Leaf)) { throw 'compose.yaml missing' }
$entries=@()
foreach ($item in @($manifest.files)) {
    $rel=[string]$item.path
    if ($rel -notmatch '^(app|static|templates|scripts|tests|migrations)/[A-Za-z0-9_./-]+$' -or $rel -match '(^|/)[.][.](/|$)' -or $rel -match '//') { throw "Unsafe path: $rel" }
    $src=Join-Path $source ($rel.Replace('/','\'))
    $dst=Join-Path $project ($rel.Replace('/','\'))
    if (!(Test-Path -LiteralPath $src -PathType Leaf)) { throw "Patch file missing: $rel" }
    $hash=(Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne ([string]$item.sha256).ToLowerInvariant()) { throw "Patch hash mismatch: $rel" }
    $exists=Test-Path -LiteralPath $dst -PathType Leaf
    $baseline=[string]$item.baseline_sha256
    if ($exists) {
        if (!$baseline -or (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $baseline.ToLowerInvariant()) { throw "Unexpected local file or changes in $rel. No overwrite." }
    } elseif ($baseline) { throw "Required baseline file missing: $rel" }
    $entries += [pscustomobject]@{Rel=$rel;Src=$src;Dst=$dst;Existed=$exists;SHA=$hash}
}
$main=Get-Content -LiteralPath (Join-Path $project 'app\main.py') -Raw -Encoding UTF8
if ($main -notmatch 'version="0[.]24[.]2"') { throw 'Expected installed Finance F3 FastAPI v0.24.2, no downgrade/reinstall.' }
Push-Location $project
try {
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Docker Compose configuration.' }
    $revision=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -ne '20261020_0014') { throw "Unexpected Alembic revision: $revision (expected 20261020_0014)" }
    Write-Host "FINANCE F4 PREFLIGHT PASS: $($entries.Count) files; Alembic $revision; no changes yet."
    if (!$Apply) { Write-Host 'PLAN ONLY: No code or database writes. Use -Apply only after reviewing.'; return }
    $stamp=Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup=Join-Path (Join-Path $HOME 'autoparts-backups') ("autocodex_finance_f4_"+$stamp+'_'+[Guid]::NewGuid().ToString('N').Substring(0,6))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    foreach ($entry in $entries) {
        if ($entry.Existed) {
            $backupDst=Join-Path $backup ($entry.Rel.Replace('/','\'))
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupDst) | Out-Null
            Copy-Item -LiteralPath $entry.Dst -Destination $backupDst -Force
        }
    }
    Write-Host "SOURCE BACKUP PASS: $backup"
    $remote="/tmp/autocodex_before_finance_f4_$stamp.dump"
    docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f $remote
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed. No code changed.' }
    docker compose exec -T db pg_restore --list $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore --list failed. No code changed.' }
    $cid=((docker compose ps -q db) | Out-String).Trim()
    if (!$cid) { throw 'Database container missing' }
    $local=Join-Path $backup 'postgres_before_finance_f4.dump'
    docker cp "${cid}:${remote}" $local
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $local -PathType Leaf) -or (Get-Item -LiteralPath $local).Length -lt 1024) { throw 'Backup copy failed. No code changed.' }
    Write-Host "DATABASE BACKUP PASS: $local"
    foreach ($entry in $entries) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $entry.Dst) | Out-Null
        Copy-Item -LiteralPath $entry.Src -Destination $entry.Dst -Force
        if ((Get-FileHash -LiteralPath $entry.Dst -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.SHA) { throw "Patch copy failed: $($entry.Rel)" }
    }
    Write-Host 'Building isolated F4 SHADOW migration + web. Worker, live payments/gateway and supplier procurement untouched.'
    docker compose up -d --build migrate web
    if ($LASTEXITCODE -ne 0) { throw 'Docker build/migration/web failed. Do NOT blindly rerun; inspect backups/log.' }
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_finance_f4
    if ($LASTEXITCODE -ne 0) { throw 'Finance F4 smoke failed. Do NOT reinstall; inspect backup/log.' }
    Write-Host "FINANCE F4 INSTALL SUCCESS: v0.24.3; Alembic 20261020_0015. Backups: $backup"
    Write-Host 'F4 is SHADOW ONLY: no real SBP, refunds, OFD, payouts, procurement or ledger postings; existing business records unchanged.'
} finally { Pop-Location }
