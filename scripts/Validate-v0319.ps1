# Read-only deployment smoke. Does not run tests, migrations, payments, or supplier writes.
$ErrorActionPreference = 'Stop'
Write-Host 'Docker services:'
docker compose ps
if ($LASTEXITCODE -ne 0) { throw 'Docker compose unavailable' }
Write-Host 'Database migration revision:'
docker compose exec -T web uv run --no-dev --no-sync alembic current
if ($LASTEXITCODE -ne 0) { throw 'Alembic failed' }
Write-Host 'Application import + syntax:'
docker compose exec -T web uv run --no-dev --no-sync python -m compileall -q app
if ($LASTEXITCODE -ne 0) { throw 'Python syntax failed' }
Write-Host 'Readiness:'
curl.exe --fail --silent --show-error 'http://127.0.0.1:8000/health/ready'
if ($LASTEXITCODE -ne 0) { throw 'Health endpoint failed' }
Write-Host "Validation finished. No real payments, refunds, or supplier purchases were initiated."
