# ROSSKO: четвертая контролируемая тестовая отправка

## Почему нужен этот тест

Третья отправка была отклонена с сообщением ROSSKO «Не указаны контактные данные». Поля contact присутствуют в Python и тестовом Zeep XML, но оператор указал только имя; в Windows PowerShell 5.1 текст, переданный через pipe внешним программам, может кодироваться как ASCII, что искажает кириллицу. **Это гипотезы, а не установленная причина.**

## Что делает инструмент

- Без изменения схемы БД использует существующие SupplierAccount/SupplierDestination и свежие GetCheckoutDetails/GetSearch.
- Требует ранее сохранённый журнал третьей попытки: `supplier_rejected` с причиной об отсутствующих контактных данных.
- Проверяет доступность Trialli CF 705 (1 шт, HST282827366, закупочная цена до 492,27 ₽), курьерский адрес, реквизиты компании.
- Только на чтение запрашивает до 500 последних GetOrders, блокирует отправку при найденном совпадении артикула. Ноль совпадений не доказывает отсутствие прежних заказов.
- Требует фамилию и имя получателя, отчество при наличии; телефон нормализуется к `+7XXXXXXXXXX`.
- Формирует Zeep SOAP XML с теми же контактами и заказом, проверяет в памяти, что contact/payment/delivery/part не потеряны и соответствуют введённым значениям. Ничего персонального не печатает.
- Атомарно создаёт постоянный журнал на volume `/srv/app/media/rossko_test_lab` **до** сетевого `GetCheckout`.
- Ровно один сетевой `GetCheckout`. Даже при таймауте автоматического повтора нет.
- Выводит обезличенное сообщение ROSSKO и полученные номера заказов. При наличии номеров отдельная `reconcile` проверяет GetOrders.

## Ограничения и риски

- Поставщик не раскрывает через эти API флаг «тестовый режим». При выключенном режиме операция может создать **реальный заказ**.
- Две предыдущие попытки и третья не имеют независимого подтверждения несуществования заказов; ограниченный GetOrders не является полной сверкой.
- Минимальная сумма курьерского заказа и тариф не установлены; поставщик может отклонить заказ.
- Это диагностическая программа, не рабочий автозакупочный модуль. Никогда не запускайте её из worker или веб-API.
- Не удаляйте и не переименовывайте журналы для повторной отправки.

## PowerShell — каждая команда одной строкой

Установить в корень проекта:

`Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\autocodex_rossko_fourth_contact_verified.zip" -DestinationPath "." -Force`

Пересобрать web:

`docker compose up -d --build --no-deps web`

Проверить UTF-8 на синтетической кириллице (без заказа):

`$OutputEncoding=[System.Text.UTF8Encoding]::new($false); [Console]::OutputEncoding=$OutputEncoding; 'Иванов Иван' | docker compose exec -T web uv run --no-dev --no-sync python -c "import sys; s=sys.stdin.readline().strip(); print('CYRILLIC_UTF8_OK=',s=='Иванов Иван')"`

**Только если CYRILLIC_UTF8_OK=True и тестовый режим на стороне ROSSKO всё ещё включён:**

`$OutputEncoding=[System.Text.UTF8Encoding]::new($false); [Console]::OutputEncoding=$OutputEncoding; $ok=Read-Host 'Тестовый режим ROSSKO включён СЕЙЧАС, риск повторного/реального заказа принят? Введите ДА'; if ($ok -cne 'ДА') { Write-Host 'Отправка отменена' } else { $n=Read-Host 'Фамилия Имя Отчество получателя (отчество при наличии)'; $p=Read-Host 'Телефон получателя'; "$n`n$p" | docker compose exec -T web uv run --no-dev --no-sync python scripts/rossko_test_contact_verified.py submit-once --account-id "511a4e77-55a7-48b8-874f-68420c7aa162" --destination-id "c6afd071-6772-4963-b3d1-0770acdbd749" --portal-test-enabled --accept-duplicate-risk --acknowledge-unknown-courier-terms --confirm I_CONFIRM_ONE_TEST_CHECKOUT_WITH_FULL_RECIPIENT_CONTACT }`

При возврате ID:

`docker compose exec -T web uv run --no-dev --no-sync python scripts/rossko_test_contact_verified.py reconcile --account-id "511a4e77-55a7-48b8-874f-68420c7aa162" --destination-id "c6afd071-6772-4963-b3d1-0770acdbd749"`

Не запускайте `submit-once` более одного раза. При неопределённом результате подтвердите статус у ROSSKO, сохраняя все журналы.
