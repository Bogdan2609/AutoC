# ROSSKO — обновлённый склад для четвертого теста (READ-ONLY FIRST)

Причина предыдущего BLOCKED BEFORE DISPATCH: в старом скрипте склад HST282827366, которого теперь нет среди свежих предложений GetSearch. Новый допустимый склад для контролируемого четвертого теста: HST181 (Trialli CF 705, 1 шт., максимальная закупочная цена 492.27 RUB). Скрипт *никогда* не подменяет склад самостоятельно; параметр --stock обязателен и должен совпадать с HST181.

Код не меняет таблицы, конфигурацию курьера или ранее сохранённые журналы. Перед каждой потенциальной отправкой перепроверяется GetCheckoutDetails, GetSearch, GetOrders и SOAP XML. GetCheckout может создать реальный заказ при неактивном тестовом режиме ROSSKO. Риск дубликата по трем предыдущим попыткам не исключён. Нельзя удалять журналы или повторять неизвестную операцию.

Установка из корня Autocodex (каждая команда — одна строка PowerShell):

```
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\autocodex_rossko_hst181_fourth_attempt_patch.zip" -DestinationPath "." -Force
```
```
docker compose up -d --build --no-deps web
```

Сначала только проверка нового склада (НЕ отправляет):

```
docker compose exec -T web uv run --no-dev --no-sync python scripts/rossko_test_checkout_lab.py preview --account-id "511a4e77-55a7-48b8-874f-68420c7aa162" --destination-id "c6afd071-6772-4963-b3d1-0770acdbd749" --stock "HST181" --payment-id 1
```

Ни одна из команд в этой инструкции не отправляет заказ. Реальный запуск уже существующей команды submit-once с --stock HST181 возможен только при осознанном подтверждении и после успешного preview. Для ФИО обязательно фамилия и имя; контакт вводится локально через stdin, не показывайте телефон в чате. Тестовый режим ROSSKO не может быть подтверждён использованными API. Также тариф и минимальная сумма курьерской доставки по филиалу пока не подтверждены.

Изменения: scripts/rossko_test_contact_verified.py — явный разрешённый склад HST181 вместо устаревшего DEFAULT_STOCK, обязательный --stock; tests/test_rossko_contact_verified.py — тест нового склада и запрета устаревшего; tests/test_rossko_one_retry_diagnostic.py — исправлена устаревшая фикстура нового формата live_quote (5 элементов). 46 профильных тестов — passed в локальном окружении, без вызовов поставщика.
