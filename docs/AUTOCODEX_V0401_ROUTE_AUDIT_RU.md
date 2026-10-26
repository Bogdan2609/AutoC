# AutoC v0.40.1 — FastAPI route-tree regression correction

**Основание:** v0.40.0, FastAPI 0.143.3, 10 failures / 720 passed / 2 skipped.

## Что исправляется
- RBAC route audit теперь использует `fastapi.routing.iter_route_contexts`, обходит вложенные `include_router`, проверяет effective path, include-time dependencies, duplicate routes и не возвращает ложный PASS при нуле исследованных API-маршрутов.
- Тесты `/admin`, `/operator` и покрытие RBAC переведены на route tree API.
- Исторический `release_gate_v039.py` **сохранён** как v0.39 gate: его версионный BLOCK на текущем релизе ожидаем, а production baseline продолжает проверяться отдельно. Актуальный `release_gate_v040.py` поднят на v0.40.1.
- Обновлён тест ручного подтверждения поставщика: до подтверждения ожидает 409 для `pending`, затем явно моделирует `manual_review` в тестовой БД; production-политика не ослаблена.
- Новые тесты на префиксы, зависимости, скрытые уязвимости аудита и дубли маршрутов.

## Не сделано автоматически
- Не выполнялся pytest / PostgreSQL race / YooKassa sandbox в пользовательском Docker.
- Не изменялись реальные БД, флаги банка и поставщиков, API-ключи, схема Alembic.
- `NO_GO` для реальных денег и автоматической закупки сохраняется.

## Проверка
1. `scripts/release_gate_v040.py` — version `0.40.1`, static PASS, live money NO_GO.
2. `tests/test_fastapi_route_tree_v0401.py`.
3. Вся регрессия `tests` в Docker с отдельным, явным `TEST_DATABASE_URL=` (SQLite); затем PostgreSQL race на одноразовой тестовой БД.
4. Особое внимание: если свежий RBAC-аудит выявит незакрытые маршруты, НЕ маскировать их — исправить реальное покрытие.
