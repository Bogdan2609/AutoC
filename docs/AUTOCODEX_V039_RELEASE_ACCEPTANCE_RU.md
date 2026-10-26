# Autocodex v0.39.0 — релизная приёмка (NO-GO для реальных денег)

**Дата:** 26.10.2026. **База:** v0.38.0. **Формат:** инкрементальный ZIP. **Миграций в v0.39.0:** нет.

## Цель и фактический результат

v0.39 — этап **верификации**, а не активации реальных закупок или эквайринга. Добавлен автоматизированный **read-only release-gate** `scripts/release_gate_v039.py`, который:

1. Проверяет версию проекта, наличие критических файлов и экспортов админки.
2. Проверяет целостность графа Alembic (статически: 37 ревизий, один корень и одна head `20261026_0037`), синтаксис Python, без исполнения миграций.
3. Не раскрывая значения секретов, проверяет опасные флаги; неизвестные значения считаются ошибкой.
4. Для `APP_ENV=production` дополнительно требует HTTPS public URL, сильные CSRF/DB-секреты и отключённый legacy admin token.
5. По флагу `--check-http` проверяет **только** `127.0.0.1:8000/health/live` и `/health/ready` без следования redirect. Визиты ограничены GET, не затрагивают банк/поставщиков и не записывают в БД.
6. Всегда указывает отдельное решение `live_money_decision=NO_GO`: локальные проверки не заменяют контракты API, sandbox и реальные PostgreSQL race-тесты.

По умолчанию сохранены **СБП-only**, **RUB-only**, **курьерская доставка поставщиков**, контакты пункта приёмки вместо покупателя, запрет на повторную отправку закупки с неопределённым исходом, выключенные боевые флаги.

## Подтверждённые проверки локальной рабочей копии

| Направление | Результат | Ограничение |
|---|---|---|
| Python source syntax | PASS | `compile()` по всем `app/*.py`; не гарантирует отсутствие runtime bugs |
| Граф Alembic | PASS | 37 ревизий, head `20261026_0037`; реальная PostgreSQL не проверялась |
| Release-gate unit tests | 11 PASS | офлайн, без реальных APIs |
| Расширенный регрессионный pytest | **721 PASS, 1 SKIPPED, 1 DESELECTED** | `test_liveness_and_admin_denied_without_token` исключён: локальный Python 3.13 не имеет psycopg; Docker требует Python 3.14 |
| TypeScript / Vite | NOT TESTED | в среде отсутствует `node_modules`; Docker build недоступен |
| Docker Compose + PostgreSQL | NOT TESTED | в рабочей среде нет Docker/PostgreSQL |
| Миграции на реальной БД | NOT TESTED | тест только на граф ревизий, нет проверки `alembic current` |
| YooKassa SBP и возврат | NOT TESTED | банковская sandbox и webhook не вызывались |
| ADEO, PARTKOM, Forum write/status/cancel | BLOCKED | договорные контракты не подтверждены и реализация не завершена |
| ROSSKO GetCheckout/GetOrders | PARTIAL | ранее добавленный безопасный ручной контур; реальную операцию в этом выпуске не выполняли |
| PostgreSQL race на нескольких соединениях | NOT TESTED | SQLite не проверяет `SELECT ... FOR UPDATE` |

**Решение: NO-GO** для боевого приёма денег и автоматической закупки. Проект можно разворачивать и проверять локально с внешними write-флагами, оставленными в `false`.

## Как проверить на Windows в PowerShell (каждая команда — одна строка)

Все команды запускать после установки ZIP в каталог `C:\Users\bogdan\Desktop\autocodex`.

1. Проверка Compose (не выводит значения секретов):

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose config --quiet
```

2. Пересборка и проверка статуса контейнеров:

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose up -d --build; if ($LASTEXITCODE -ne 0) { throw 'Docker compose failed' }; docker compose ps
```

3. Read-only gate внутри web-контейнера:

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose exec -T web python scripts/release_gate_v039.py --check-http
```

4. Фактическая ревизия БД (должна совпасть с `20261026_0037` после миграций v0.37):

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose exec -T web uv run --no-dev --no-sync alembic current
```

5. Вывод диагностических логов — **не отправлять содержимое `.env` и секреты**:

```powershell
Set-Location 'C:\Users\bogdan\Desktop\autocodex'; docker compose logs --tail=100 migrate web worker
```

Внимание: `.env` не должен попадать в ZIP/репозиторий, но файл **должен существовать на машине пользователя**, так как его требует Compose. `--check-http` внутри контейнера работает без `.env` в образе, поскольку конфигурация поступает через Docker environment.

## Обязательная оставшаяся приёмка перед GO

- **Окружение:** успешные Docker build с Python 3.14, TS build, работоспособные `web`, `worker`, `db`, `redis`, отсутствие миграционных конфликтов и ошибок старта.
- **Тесты:** полный pytest внутри совместимого окружения; отдельная **одноразовая тестовая БД** для конкурентных транзакций. Никогда не направлять `TEST_DATABASE_URL` на пользовательскую основную БД: тестовая фикстура делает `drop_all/create_all`.
- **Платёж:** тестовые сценарии СБП YooKassa — succeeded/canceled/pending, повтор/параллельные webhook, payment reconciliation, полный/частичный refund, lost response, неверные RUB/сумма, возврат без двойного POST.
- **Закупки:** контракты ADEO, PARTKOM, Forum для create/status/cancel (включая отсутствие отмены), подтверждение валюты; ROSSKO — корректное сопоставление GetOrders и строго проверенные реквизиты курьерской доставки. Контакты и комментарий заявки — из пункта приёмки; `PARTS.Part.comment` — UUID локального заказа.
- **Конкурентность:** два процесса/соединения POST webhook для одного и двух платежей, одновременные отмены/возвраты, остановка worker между внешним вызовом и commit, повторный claim зависшей задачи.
- **Граница сети:** TLS, доверенные прокси, whitelist / reverse-proxy security policy для YooKassa webhook, отдельные лимиты multipart/CSV/uploads, ротация секретов, резервное копирование и тест восстановления.
- **Мониторинг:** алерты для очередей `manual_review`, зависших платежей/закупок и ошибок worker. Проверка, что админ видит незавершённые операции и не получает избыточных прав.
- **Экономика:** ручное согласование правил фидов после v0.37; стоимость доставки до покупателя, НДС/налоговый режим, комиссия СБП, минимальная маржа.

Если любой из этих пунктов не подтверждён, финансовый go-live остаётся закрытым. Не отключать предохранители ради прохождения приёмки.

## Как читать результат release-gate

`PASS`: локально проверено; `BLOCK`: невозможно пройти статическую проверку; `NOT_TESTED`: тест ещё предстоит выполнить. Значение `static_status=PASS` **не** означает `live_money_decision=GO`.

Реальные внешние запросы сознательно отсутствуют; никакие sandbox-учётные данные, банковские токены или личные данные не включены в ZIP.
