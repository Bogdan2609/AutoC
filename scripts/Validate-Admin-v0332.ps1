# Read-only: checks deployed Python routes and health; no checkout or payment calls.
$ErrorActionPreference = 'Stop'
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose config invalid' }
docker compose exec -T web uv run --no-dev --no-sync python -c "import ast,pathlib; files=list(pathlib.Path('app').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p)) for p in files]; print('PYTHON_AST_OK',len(files))"
if ($LASTEXITCODE -ne 0) { throw 'Python syntax invalid' }
docker compose exec -T web uv run --no-dev --no-sync python -c "from app.main import app; need={'/api/v1/backoffice/monitor/attention','/api/v1/backoffice/monitor/receiving-readiness','/api/v1/backoffice/monitor/configuration','/api/v1/backoffice/monitor/orders','/api/v1/backoffice/monitor/purchases','/api/v1/backoffice/monitor/jobs'}; present={path for path,operations in app.openapi().get('paths',{}).items() if 'get' in operations}; assert need.issubset(present),sorted(need-present); print('ADMIN_ROUTES_OK',len(need))"
if ($LASTEXITCODE -ne 0) { throw 'Operator endpoints unavailable' }
docker compose exec -T web uv run --no-dev --no-sync alembic current
if ($LASTEXITCODE -ne 0) { throw 'Alembic check failed' }
curl.exe --fail --silent --show-error http://127.0.0.1:8000/health/ready
if ($LASTEXITCODE -ne 0) { throw 'Application not ready' }
Write-Host 'ADMIN V0332 CHECKS OK. No financial or supplier write was performed.'
