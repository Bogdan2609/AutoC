param([Parameter(Mandatory=$true)][string]$ProjectDir,[Parameter(Mandatory=$true)][string]$SourceDir)
$ErrorActionPreference='Stop'
$project=(Resolve-Path -LiteralPath $ProjectDir).Path
$source=(Resolve-Path -LiteralPath $SourceDir).Path
$manifestPath=Join-Path $source 'PATCH_MANIFEST_V018.json'
if(!(Test-Path -LiteralPath $manifestPath)){throw 'Missing v0.18 patch manifest'}
$manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if($manifest.version -ne '0.18.0'){throw 'Wrong patch manifest version'}
if(!(Test-Path -LiteralPath (Join-Path $project 'compose.yaml'))){throw 'compose.yaml not found'}
if(!(Test-Path -LiteralPath (Join-Path $project 'scripts\smoke_v017.py'))){throw 'Expected working v0.17 baseline'}
$files=@()
foreach($entry in $manifest.files){
    $relative=[string]$entry.path
    if([string]::IsNullOrWhiteSpace($relative) -or $relative.StartsWith('/') -or $relative.Contains('..') -or $relative.Contains(':')){throw "Unsafe manifest path: $relative"}
    $from=Join-Path $source ($relative.Replace('/','\'))
    if(!(Test-Path -LiteralPath $from)){throw "Incomplete v0.18 package: $relative"}
    $actual=(Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash.ToLowerInvariant()
    if($actual -ne ([string]$entry.sha256).ToLowerInvariant()){throw "SHA256 mismatch: $relative"}
    $files+=,$relative
}
$backup=Join-Path (Join-Path $HOME 'autoparts-backups') ('autoparts_v018_'+(Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$created=@()
foreach($relative in $files){
    $target=Join-Path $project ($relative.Replace('/','\'))
    if(Test-Path -LiteralPath $target){
        $saved=Join-Path $backup ($relative.Replace('/','\'))
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $saved) | Out-Null
        Copy-Item -LiteralPath $target -Destination $saved -Force
    }else{$created+=,$relative}
}
$sourceChanged=$false
Push-Location $project
try {
    Write-Host 'Preflight v0.17 ASGI smoke (read-only)...'
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v017
    if($LASTEXITCODE -ne 0){throw 'v0.17 smoke failed; aborting'}
    $current=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') -join "`n").Trim()
    if($LASTEXITCODE -ne 0 -or $current -ne '20261019_0006'){throw "Expected Alembic 20261019_0006, found $current"}
    Write-Host 'Backing up PostgreSQL (custom-format pg_dump) before schema migration...'
    docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f /tmp/autoparts_v018_before.dump
    if($LASTEXITCODE -ne 0){throw 'pg_dump failed; no changes made'}
    docker compose cp db:/tmp/autoparts_v018_before.dump (Join-Path $backup 'postgres_before_v018.dump')
    if($LASTEXITCODE -ne 0){throw 'Could not retrieve PostgreSQL backup; no changes made'}
    docker compose exec -T db rm -f /tmp/autoparts_v018_before.dump
    $dump=Join-Path $backup 'postgres_before_v018.dump'
    if(!(Test-Path -LiteralPath $dump) -or (Get-Item -LiteralPath $dump).Length -lt 1024){throw 'Backup file missing or unexpectedly small; no source changes made'}
    $sourceChanged=$true
    foreach($relative in $files){
        $target=Join-Path $project ($relative.Replace('/','\'))
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath (Join-Path $source ($relative.Replace('/','\'))) -Destination $target -Force
    }
    Write-Host 'Applying v0.18 and migration 20261019_0007...'
    docker compose up -d --build
    if($LASTEXITCODE -ne 0){throw 'Docker build, migration or start failed'}
    $current=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') -join "`n").Trim()
    if($LASTEXITCODE -ne 0 -or $current -ne '20261019_0007'){throw "Wrong Alembic revision after deploy: $current"}
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v018
    if($LASTEXITCODE -ne 0){throw 'v0.18 web smoke failed'}
    docker compose exec -T worker /srv/app/.venv/bin/python -m scripts.smoke_v018
    if($LASTEXITCODE -ne 0){throw 'v0.18 worker smoke failed'}
    docker compose exec -T web /srv/app/.venv/bin/python -m scripts.smoke_v017
    if($LASTEXITCODE -ne 0){throw 'v0.17 backwards-compatibility smoke failed'}
    docker compose ps -a
    Write-Host "v0.18 installed. Alembic: $current. Backup directory: $backup"
    Write-Host 'Installer made no provider calls, customer orders, supplier purchases or payments.'
} catch {
    Write-Warning ('v0.18 deployment failed: '+$_)
    if($sourceChanged){
        $safeRollback=$true
        Write-Warning 'Attempting schema rollback and restoration of old sources...'
        try {
            $dbVersion=((docker compose exec -T db psql -U autoparts -d autoparts -At -c 'SELECT version_num FROM alembic_version') -join "`n").Trim()
            if($LASTEXITCODE -ne 0){throw 'Cannot inspect current schema revision'}
            if($dbVersion -eq '20261019_0007'){
                docker compose stop web worker
                if($LASTEXITCODE -ne 0){throw 'Failed to stop app before schema rollback'}
                docker compose run --rm migrate uv run --no-dev --no-sync alembic downgrade 20261019_0006
                if($LASTEXITCODE -ne 0){throw 'Alembic downgrade failed'}
            }elseif($dbVersion -ne '20261019_0006'){throw "Unexpected Alembic revision: $dbVersion"}
        }catch{
            $safeRollback=$false
            Write-Warning ('Database downgrade cannot be proven: '+$_)
            Write-Warning 'New source preserved to avoid a schema/source mismatch. Investigate the PostgreSQL revision and backup manually.'
        }
        if($safeRollback){
            foreach($relative in $files){
                $target=Join-Path $project ($relative.Replace('/','\'))
                $saved=Join-Path $backup ($relative.Replace('/','\'))
                if(Test-Path -LiteralPath $saved){Copy-Item -LiteralPath $saved -Destination $target -Force}
                elseif($created -contains $relative){Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue}
            }
            Write-Warning 'Previous v0.17 source restored. Rebuilding corresponding Docker images...'
            try {docker compose up -d --build;if($LASTEXITCODE -ne 0){Write-Warning 'Previous Docker rebuild failed; inspect docker compose ps -a'}}
            catch {Write-Warning ('Previous Docker rebuild error: '+$_)}
        }
    }
    throw
} finally {Pop-Location}
