param(
  [Parameter(Mandatory=$true)][string]$ProjectDir,
  [Parameter(Mandatory=$true)][string]$SourceDir
)
$ErrorActionPreference = 'Stop'
$ProjectDir = (Resolve-Path $ProjectDir).Path
$SourceDir = (Resolve-Path $SourceDir).Path
if (!(Test-Path (Join-Path $ProjectDir '.env'))) { throw 'Existing .env not found. Do not use a default .env for a deployed project.' }
if (!(Test-Path (Join-Path $SourceDir 'migrations\versions\20261019_0005_marketplace_014.py'))) { throw 'Incorrect v0.14 source folder.' }
Set-Location $ProjectDir
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $HOME "autoparts-backups\pre_v014_$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$items = @('app','migrations','templates','static','tests','docs','scripts',
  'pyproject.toml','compose.yaml','Dockerfile','README.md','alembic.ini','.env.example')
$existing = @($items | Where-Object { Test-Path (Join-Path $ProjectDir $_) } | ForEach-Object { Join-Path $ProjectDir $_ })
if ($existing.Count -eq 0) { throw 'No project files found' }
Compress-Archive -Path $existing -DestinationPath (Join-Path $backup 'source_before_v014.zip') -CompressionLevel Optimal
$dbid = (docker compose ps -q db)
if ($LASTEXITCODE -ne 0 -or !$dbid) { throw 'Database container not running' }
$rev = (docker compose exec -T db psql -U autoparts -d autoparts -Atc 'SELECT version_num FROM alembic_version;').Trim()
if ($LASTEXITCODE -ne 0 -or $rev -notin @('20261017_0004','20261019_0005')) { throw "Unsupported Alembic revision: $rev. No files were changed." }
# Backup *before* writing source code or executing migrations.
docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f /tmp/pre_v014.dump
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed. No files were changed.' }
docker cp "${dbid}:/tmp/pre_v014.dump" (Join-Path $backup 'postgres_before_v014.dump')
if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed. No files were changed.' }
foreach($name in $items) {
  $src = Join-Path $SourceDir $name
  if (!(Test-Path $src)) { continue }
  $dst = Join-Path $ProjectDir $name
  if ((Get-Item $src).PSIsContainer) {
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
      Copy-Item -LiteralPath $_.FullName -Destination $dst -Recurse -Force
    }
  } else {
    Copy-Item -LiteralPath $src -Destination $dst -Force
  }
}
Write-Host "Backup saved: $backup"
# .env, PostgreSQL Docker volume, Redis Docker volume and active supplier accounts are untouched.
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
  docker compose logs --no-color --tail=120 migrate
  throw "Compose startup failed. Source and DB backups available at $backup"
}
$rev2 = (docker compose exec -T db psql -U autoparts -d autoparts -Atc 'SELECT version_num FROM alembic_version;').Trim()
if ($LASTEXITCODE -ne 0 -or $rev2 -ne '20261019_0005') { throw "Unexpected Alembic revision: $rev2" }
docker compose ps -a
docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v014
if ($LASTEXITCODE -ne 0) { throw 'Smoke verification failed' }
Write-Host 'Autoparts v0.14 source installed. No supplier orders have been sent by this script.'
