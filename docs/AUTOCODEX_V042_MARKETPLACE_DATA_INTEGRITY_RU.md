# AutoC v0.42.0 — Marketplace & Data Integrity

Выпуск поверх **v0.41.2**, исходный согласованный roadmap v0.40–v0.44. Никакого переноса v0.42 в courier checkout.

## Реализовано в этом пакете

- **M42-01** Исправлен `origin_node_id`: в v017 снимок попадает проверенный UUID `receiving_node_id`, а не читаемое название узла. Название остаётся в `receiving_node` для UI. Исторические заказы не переписываются.
- **M42-02 (часть)** Ограничено количество строк корзины 40, preview читает максимум 41 для явного отказа. GET preview ограничены по authenticated user ID + реальному peer IP в Redis (30/мин/маршрут); недоступный Redis -> 503 кроме test mode. Синхронная обработка текущих товаров сохранена. Требуются PostgreSQL EXPLAIN/query-count профили до завершения N+1-оптимизации.
- **M42-03 (часть)** При отмене legacy v017 проверяется исходный inventory movement по ключу резервирования, балансу и количеству. Сломанные остатки отклоняются с 409 вместо уменьшения чужого резерва. Добавлен ограниченный read-only операторский отчёт по legacy reservation_key. Массовый release и исторический backfill не активированы.
- **M42-05 (часть)** Для quote запрещены дробные копейки/NaN/Infinity/отрицательные значения. Флаг известности доставки сам по себе не создаёт payable grand_total: нужен явный размер тарифа. Итоговая оплата по preview **всё равно запрещена**.
- **M42-09 (часть)** Стриминг распакованного ZIP ограничен фактически прочитанными байтами, включая XML; ограничены shared strings, число строк и ячеек ROSSKO XLSX. Семантику колонки «Кратность отгрузки» не меняли без подтверждения API.
- **M42-10 (часть)** Read-only аудит старых OfferSnapshot: исключение ссылок из OrderLine/CartLine/PurchaseAttempt и сохранение самого нового snapshot для exact supplier item + account + destination. Удаление **выключено**, требуется отдельная проверка и операторский approval после PostgreSQL concurrency acceptance.

## Новые административные GET, без изменения данных

- `GET /api/v1/admin/marketplace/data-integrity/offer-history?days=180&limit=50` (90..3650 дней, 1..100 строк).
- `GET /api/v1/admin/marketplace/data-integrity/legacy-reservations?limit=100` (1..200 результатов из последних 5000 commercial snapshots; возможна неполнота).

Оба под `require_admin` + существующая RBAC policy. Ни один endpoint не вызывает поставщика, не удаляет историю и не переводит статус заказа.

## Неполные задачи оригинального v0.42 (не заявлять COMPLETE)

- M42-02: доказать query count и полностью устранить N+1 в `marketplace_offers`, `cart`, `v017/preview` на PostgreSQL.
- M42-03: безопасный административный workflow старых резервов с отдельным audit и collision handling.
- M42-04: live quote непосредственно перед СБП (по-прежнему NO-GO для live денег); UX refresh новых snapshots.
- M42-05: бизнес-порог min_contribution и ceiling checks во всех каналах публикации, после согласования финансовой политики.
- M42-06: налоговые THRESHOLDS_2026/FUTURE — справочные, налоговые ставки остаются simulation; автоматическое решение требует юридической проверки.
- M42-07: expanded malformed feed/adapter compatibility tests.
- M42-08: смысл ROSSKO XLSX `Кратность отгрузки` минимум/шаг требует подтверждения поставщика.
- M42-09: экстремальные memory/CPU ZIP tests и полная защита по wall-clock бюджету не проверены.
- M42-10: фактическое архивирование/удаление выключено; подтверждение индексов/коммерческих ссылок на PostgreSQL обязательно.

## Инварианты

- Только СБП, без холдов. Реальные деньги, возвраты и закупки NO-GO.
- Поставщики доставляют **только курьером** на ранее подтверждённые склады/ПВЗ.
- В закупке всегда контакт склада, не покупателя; ROSSKO part.comment = UUID внутреннего заказа.
- Исторические коммерческие snapshots неизменяемы. При неизвестных/неполных условиях `grand_total=None`.
- Никаких внешних WRITE или БД-миграций при установке; резервные копии каждого изменённого исходника.

## Проверки

1. Docker `--check` исходников до применения.
2. Docker compose `up -d --build` и `ps`, `/health/live`, `/health/ready`.
3. `pytest tests/test_marketplace_integrity_v042.py -q -p no:cacheprovider` (в контейнере).
4. Полная регрессия `pytest tests -q` в контейнере с изолированной TEST_DATABASE_URL.
5. PostgreSQL independent-connection races и реальный baseline SQL profile — до полного закрытия v0.42.
