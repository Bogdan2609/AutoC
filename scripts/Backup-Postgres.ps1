param([string]$Destination = '.\backups')
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$name = "autoparts-$timestamp.dump"
$remote = "/tmp/$name"
$local = Join-Path (Resolve-Path $Destination) $name
try {
    docker compose exec -T db pg_dump -U autoparts -d autoparts -Fc -f $remote
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
    docker compose cp "db:$remote" $local
    if ($LASTEXITCODE -ne 0) { throw 'Could not download the database backup' }
    $hash = (Get-FileHash -Algorithm SHA256 $local).Hash
    Write-Output "Backup: $local"
    Write-Output "SHA256: $hash"
} finally {
    docker compose exec -T db rm -f $remote | Out-Null
}
