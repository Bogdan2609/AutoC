param([Parameter(Mandatory=$true)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path $BackupPath).Path
# This script never modifies the application's autoparts database.
$name = "audit_restore_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
$remote = "/tmp/$name.dump"
$created = $false
try {
    docker compose cp $source "db:$remote"
    if ($LASTEXITCODE -ne 0) { throw 'Failed to copy backup into PostgreSQL container' }
    docker compose exec -T db pg_restore -l $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Backup file is unreadable' }
    docker compose exec -T db createdb -U autoparts $name
    if ($LASTEXITCODE -ne 0) { throw 'Could not create disposable restore database' }
    $created = $true
    docker compose exec -T db pg_restore -U autoparts --no-owner --no-privileges -d $name $remote
    if ($LASTEXITCODE -ne 0) { throw 'Database restore failed' }
    docker compose exec -T db psql -U autoparts -d $name -v ON_ERROR_STOP=1 -Atc "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public';"
    if ($LASTEXITCODE -ne 0) { throw 'Restore smoke check failed' }
    Write-Output "Restore OK into temporary database: $name"
} finally {
    if ($created) {
        docker compose exec -T db dropdb -U autoparts --if-exists $name | Out-Null
    }
    docker compose exec -T db rm -f $remote | Out-Null
}
