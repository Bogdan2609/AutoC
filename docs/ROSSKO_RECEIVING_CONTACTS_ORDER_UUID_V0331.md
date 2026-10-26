# Autocodex v0.33.1 — контакты точки приёмки и UUID заказа ROSSKO

## Правило

- `GetCheckout.contact.name`, `contact.phone`, `contact.comment` берутся **только** из `fulfillment_nodes` выбранного `SupplierDestination.node_id`.
- ФИО и телефон обязательны и проверяются перед закупкой; комментарий необязателен (`""` допустимо), сохраняется на складе/ПВЗ и уходит в SOAP.
- Никакого подхвата имени или телефона покупателя (`Order`, `User`, `Cart`) либо из HTTP запроса оформления.
- У каждого `GetCheckout.PARTS.Part.comment` — UUID **покупательского заказа** из `customer_orders.id`, одинаковый для всех позиций этого заказа, без сокращений (36 символов, лимит ROSSKO — 50).
- Для отдельного **тестового** GetCheckout, не связанного с реальным заказом покупателя, создаётся внутренний UUID тестового заказа и сохраняется в защищённом журнале; он не выдаётся за UUID оплаченного заказа.
- Старые скрипты `rossko_test_checkout_lab.py`, `rossko_test_retry_diagnostic.py` и `rossko_test_requisite_confirm.py` сохраняют просмотр и сверку; их старые команды отправки **запрещены**, потому что не соответствуют новому контракту.

## Изменения БД

- `20261023_0032`: `fulfillment_nodes.receiving_contact_name`, `receiving_contact_phone`, `receiving_contact_comment`.
- `20261023_0033`: `supplier_purchase_attempts.buyer_order_id`, `contact_comment`.
- Старые адреса и договорные реквизиты сохраняются; в существующих точках пустые контакты не заполняются данными покупателей. До настройки получателя заказ поставщику **блокируется**.
- После обновления текущий head Alembic: `20261023_0033`. Обновлять БД только после резервного копирования.

## Настройка в административном API

- `PATCH /api/v1/admin/marketplace/nodes/{node_id}` с `receiving_contact_name`, `receiving_contact_phone`, `receiving_contact_comment`.
- Для очистки комментария достаточно PATCH `{"receiving_contact_comment":""}`. ФИО и телефон меняются одной операцией.
- `GET /api/v1/admin/marketplace/nodes/{node_id}/receiving-contact` — доступен только администратору, показывает текущие значения для редактирования.
- В рабочей админке `/operator` → «Склады и ПВЗ» доступны поля для создания и изменения контактов. В списке показывается только признак настройки контакта; ФИО, телефон и комментарий видны администратору в форме редактирования.
- Самовывоз у поставщика НЕ включается: только настроенная курьерская доставка на склад/ПВЗ.

## Настройка без ввода контактных данных в чат

Выполнять в **Windows PowerShell** из корня проекта. Каждая команда — в одну строку.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Backup-Postgres.ps1 -Destination .\backups
```

После распаковки архива патча:

```powershell
docker compose build web worker migrate
```
```powershell
docker compose run --rm migrate
```
```powershell
docker compose up -d web worker
```

Проверка статуса контактов для направления ROSSKO (не раскрывает контакт):

```powershell
docker compose exec -T web uv run --no-dev --no-sync python scripts/Configure-ReceivingContact.py status --destination-id "c6afd071-6772-4963-b3d1-0770acdbd749"
```

Сохранение контактов **одного склада/ПВЗ** (имя, телефон и необязательный комментарий запрашиваются интерактивно; в чат передавать не нужно):

```powershell
$OutputEncoding=[System.Text.UTF8Encoding]::new($false); [Console]::OutputEncoding=$OutputEncoding; $n=Read-Host 'ФИО получателя склада/ПВЗ'; $p=Read-Host 'Телефон склада/ПВЗ'; $c=Read-Host 'Комментарий contact (можно пустой)'; "$n`n$p`n$c" | docker compose exec -T web uv run --no-dev --no-sync python scripts/Configure-ReceivingContact.py set --destination-id "c6afd071-6772-4963-b3d1-0770acdbd749"
```

Контакт хранится в `fulfillment_nodes`: все направления поставщиков, ведущие на тот же склад, получают эти данные автоматически. Для разных складов — разные контакты.

## Проверки и ограничения

- Офлайн-тесты проверяют обязательность складских контактов, необязательность `contact.comment`, отсутствие покупательских данных, длину и точность UUID, формирование рабочего payload и неизменность запрета повторного `GetCheckout`.
- Действующие заказы не размещались и настоящие API поставщиков из тестового окружения не вызывались.
- Подключение и ручная публикация товаров никак не затронуты; боевые переключатели СБП и закупок не менялись.
- После выката проверьте миграции и `health/ready` в своем Docker, затем настройте имя/телефон на складе до первой реальной закупки.
