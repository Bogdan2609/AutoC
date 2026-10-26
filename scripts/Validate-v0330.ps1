# Read-only local validation. Syntax parsing must NOT create __pycache__ on bind mounts.
$ErrorActionPreference = 'Stop'
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose config invalid' }
docker compose ps
if ($LASTEXITCODE -ne 0) { throw 'Docker unavailable' }
docker compose exec -T web uv run --no-dev --no-sync alembic current
if ($LASTEXITCODE -ne 0) { throw 'Alembic check failed' }
docker compose exec -T web uv run --no-dev --no-sync python -c "import ast,pathlib; files=sorted(pathlib.Path('app').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p)) for p in files]; print('Python syntax OK:',len(files),'files')"
if ($LASTEXITCODE -ne 0) { throw 'Python syntax failed' }
docker compose exec -T web uv run --no-dev --no-sync python -c "from app.modules.marketplace.purchase_gateway import PurchaseIntent, build_purchase_plan; from app.modules.offer_engine.providers import require_rub; require_rub('RUB'); print('supplier contracts imported')"
if ($LASTEXITCODE -ne 0) { throw 'Supplier contract check failed' }
curl.exe --fail --silent --show-error http://127.0.0.1:8000/health/ready
if ($LASTEXITCODE -ne 0) { throw 'Readiness failed' }
Write-Host 'Read-only checks completed. No payments or supplier purchases were sent.'
