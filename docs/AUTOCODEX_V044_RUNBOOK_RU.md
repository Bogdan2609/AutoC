# AutoC v0.44 — Windows PowerShell, каждая команда в одну строку

Скачайте ZIP, поместите в `C:\Users\bogdan\Desktop\autocodex\v044.zip`. ZIP не распаковывать вручную.

## Установка

**1. Preflight — никаких изменений:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker run --rm -v "$($PWD.Path):/workspace" -w /workspace python:3.14.8-slim python -c "import zipfile,runpy,sys; zipfile.ZipFile('v044.zip').extractall('/tmp/v044'); sys.path.insert(0,'/tmp/v044'); sys.argv=['/tmp/v044/apply_v044.py','--root','/workspace','--check']; runpy.run_path(sys.argv[0],run_name='__main__')"
```

**2. Установка строго после PASS:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker run --rm -v "$($PWD.Path):/workspace" -w /workspace python:3.14.8-slim python -c "import zipfile,runpy,sys; zipfile.ZipFile('v044.zip').extractall('/tmp/v044'); sys.path.insert(0,'/tmp/v044'); sys.argv=['/tmp/v044/apply_v044.py','--root','/workspace','--apply']; runpy.run_path(sys.argv[0],run_name='__main__')"
```

**3. Docker build:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose up -d --build; if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }; docker compose ps
```

**4. Новые тесты:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose exec -T -e TEST_DATABASE_URL= -e AUTOCODEX_TEST_PG_URL= web uv run --no-dev --no-sync --with pytest --with pytest-asyncio --with aiosqlite python -m pytest tests/test_preproduction_acceptance_v044.py -q -p no:cacheprovider
```

**5. Полная регрессия (без рабочей БД):**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose exec -T -e TEST_DATABASE_URL= -e AUTOCODEX_TEST_PG_URL= web uv run --no-dev --no-sync --with pytest --with pytest-asyncio --with aiosqlite python -m pytest tests -q -p no:cacheprovider
```

**6. Technical gate:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose exec -T web python scripts/release_gate_v044.py
```

**7. Offline acceptance report (в рабочем каталоге, без API):**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker run --rm -v "$($PWD.Path):/workspace" -w /workspace python:3.14.8-slim python scripts/acceptance_report_v044.py --output /workspace/acceptance_v044_report.json
```

## Отдельная PostgreSQL 18.6 для race-тестов

**8. Создать временный PG. Никакой публикации порта, только tmpfs:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker run -d --name autocodex-pgtest-v044 --tmpfs /var/lib/postgresql:rw,size=512m -e POSTGRES_USER=autoc_test -e POSTGRES_PASSWORD=temporary-local-v044 -e POSTGRES_DB=autoc_test_v044 postgres:18.6
```

**9. Готовность:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; $ready=$false; for ($i=0; $i -lt 30; $i++) { docker exec autocodex-pgtest-v044 pg_isready -U autoc_test -d autoc_test_v044; if ($LASTEXITCODE -eq 0) { $ready=$true; break }; Start-Sleep -Seconds 1 }; if (-not $ready) { throw 'Disposable PostgreSQL not ready' }
```

**10. Тесты PostgreSQL на одноразовой базе:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker run --rm --network container:autocodex-pgtest-v044 -w /srv/app --entrypoint uv -e APP_ENV=test -e SUPPLIER_API_ENABLED=false -e SUPPLIER_PROCUREMENT_ENABLED=false -e REAL_SUPPLIER_CHECKOUT_ENABLED=false -e TEST_DATABASE_URL= -e AUTOCODEX_TEST_PG_URL=postgresql+psycopg://autoc_test:temporary-local-v044@127.0.0.1:5432/autoc_test_v044 autocodex-web run --no-dev --no-sync --with pytest --with pytest-asyncio --with aiosqlite python -m pytest tests/test_postgres_preproduction_v044.py -q -rs -p no:cacheprovider
```

**11. Удалить временную БД после проверки:**

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker rm -f -v autocodex-pgtest-v044
```

**Важно:** выше только локальные unit/PG тесты. Sandbox YooKassa, офферы/заказы поставщиков, HTTPS ingress, юридические вопросы, фискализация, backup/restore, нагрузка и реальные миграции **не заменяются** этими командами и требуют отдельной preproduction среды/доказательств. Никакого реального платежа, возврата или закупки эти команды не запускают. Итоговое решение всегда `NO_GO` до отдельного человеческого утверждения.
