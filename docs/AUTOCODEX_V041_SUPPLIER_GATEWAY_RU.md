# AutoC v0.41.0 — Supplier Integration Gateway

## Назначение

Единая фабрика **read-only** адаптеров с подтверждённым источником данных о возможностях. Это не новая реализация HTTP/SOAP протоколов: используем существующие проверенные `offer_engine.providers.PROVIDERS` и `marketplace.purchase_lifecycle.capabilities`.

## Фактическая интеграция

1. `SupplierRegistry` смотрит на прежние singleton-адаптеры, не копирует их и не создаёт второй сетевой стек.
2. `SupplierFactory.for_search()` связывает адаптер с конкретными Supplier, SupplierAccount, SupplierDestination, FulfillmentNode. Проверяются принадлежность, включённые флаги, верификация узла и готовность учётных данных.
3. `workers.runner.search()` вызывает фабрику. Существующие лимиты Redis, lease поисковой задачи, per-account CircuitBreaker, повторная проверка состояния аккаунта после HTTP и запись снимков **сохранены**.
4. `SupplierBinding.search` и `check_stock` — только чтение. Для поддельных адаптеров из тестов сохранена обратная совместимость.
5. `SupplierBinding.preview_purchase()` использует существующий чистый план и проверку courier-only; результат `dispatch_authorized=False`.
6. Все write-операции фабрики запрещены (`SupplierWriteForbidden`). ROSSKO ручной контур продолжает работать только через прежний платёжно-закупочный доменный барьер, со своими дополнительными разрешениями и неизменяемой фиксацией `dispatch_unknown`.
7. Админ API возвращает матрицу и конфигурационную готовность без API-секретов, buyer PII или сетевых запросов. В шапке «Поставщики → Центр интеграций» появился раздел возможностей API.

## Структура

- `app/integrations/suppliers/registry.py` — четыре известных supplier code и read-through реестр.
- `app/integrations/suppliers/capabilities.py` — возможности на основе `purchase_lifecycle`, не отдельная таблица.
- `app/integrations/suppliers/factory.py` — связывание аккаунта/маршрута и read-only методы.
- `app/integrations/suppliers/diagnostics.py` — безопасная redacted-диагностика.
- `app/modules/marketplace/account_probe.py` — read-only admin endpoints.
- `tests/test_supplier_factory_v041.py` — новые тесты (без сетевых вызовов).

## Матрица возможностей, не обещание live-работы

| Поставщик | Search + Stock Check | Preview | Подтверждён create contract | Readback contract | Cancel verified | Gateway write |
|---|---|---|---|---|---|---|
| ROSSKO | да | да | в ранее существующем ручном контуре | да, GetOrders с ограничениями | нет | **нет** |
| ADEO | да | да | нет | нет | нет | **нет** |
| PARTKOM | да | да | нет | нет | нет | **нет** |
| FORUM-AUTO | да | да | нет | нет | нет | **нет** |

* `contract_live_create=True` у ROSSKO — свойство кода и контракта, **не признак допуска к боевой закупке**.
* Ни один timeout на write не должен привести к автоматическому повтору POST.
* Нет fallback между SupplierAccount/Destination при закупке после СБП.
* Документация по POST заказа, статусу, отмене, полям идемпотентности и курьерским адресам требуется для последующего отдельного релиза live закупок.

## API

- `GET /api/v1/admin/marketplace/supplier-integrations/capabilities` — матрица операций, `read_only=true`.
- `GET /api/v1/admin/marketplace/supplier-accounts/{account_id}/integration-readiness` — локальная готовность всех привязанных узлов, без обращения к поставщику.

## Безопасность и ограничения

- СБП-only, RUB-only, courier-only, контакты из точки приёмки, ручная фиксация поставки — без изменений.
- Никакой новой миграции и новых флагов реального заказа. `.env` не изменяется.
- Factory НЕ является альтернативой `marketplace.procurement` или его защищённым операциям.
- `readiness=ready` означает лишь **локальную конфигурацию**; не факт доступности API, цены или реального наличия.
- В `repr` binding секреты не показываются; адрес, ФИО и телефон вообще не входят в binding.
- Незакрытые PostgreSQL race-тесты v0.40 и live_money NO-GO остаются.
- Проверки npm/Vite, pytest и Docker/PostgreSQL должны выполняться после установки на целевой машине.
