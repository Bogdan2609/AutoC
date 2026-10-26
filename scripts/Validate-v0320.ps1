# Read-only deployment smoke. Does not trigger purchasing, payment or publishing.
$ErrorActionPreference = 'Stop'
Write-Host 'Checking Docker Compose:'
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration invalid' }
Write-Host 'Container status:'
docker compose ps
if ($LASTEXITCODE -ne 0) { throw 'Docker unavailable' }
Write-Host 'Database schema version (expected head 20261022_0030):'
docker compose exec -T web uv run --no-dev --no-sync alembic current
if ($LASTEXITCODE -ne 0) { throw 'Alembic revision unavailable' }
Write-Host 'Python compilation:'
docker compose exec -T web uv run --no-dev --no-sync python -m compileall -q app
if ($LASTEXITCODE -ne 0) { throw 'Syntax error' }
Write-Host 'Import of unified sales workflow:'
docker compose exec -T web uv run --no-dev --no-sync python -c "from app.modules.commerce.sale_lifecycle import SaleLineExecution; from app.modules.marketplace.procurement import SupplierPurchaseAttempt; print('sales workflow imported')"
if ($LASTEXITCODE -ne 0) { throw 'Cannot import unified sales workflow' }
Write-Host 'Readiness:'
curl.exe --fail --silent --show-error http://127.0.0.1:8000/health/ready
if ($LASTEXITCODE -ne 0) { throw 'Health check failed' }
Write-Host 'Verified read-only. No real purchases, payment or publication.'
