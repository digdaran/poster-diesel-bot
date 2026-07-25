# ARCHITECTURE.md — архитектура платформы розыгрышей

> Источник требований — `ТЗ_Raffle_Platform.md`. Здесь описывается, как ТЗ реализовано технически.

## 1. Процессы

| Процесс | Образ | Технология | Роль |
|---|---|---|---|
| `backend` | `docker/backend.Dockerfile` | FastAPI + Uvicorn | REST API панели, фоновые задачи (сверка платежей по банковской выписке, освобождение просроченных резервов), `/metrics`. Вебхуки банков-эквайеров удалены вместе с интернет-эквайрингом (DECISIONS.md №44) — подтверждение оплаты только через сверку выписки. |
| `channel-telegram` | `docker/telegram.Dockerfile` | aiogram 3, long polling | Активный в проде мессенджер-канал (наравне с `channel-vk`). |
| `channel-vk` | `docker/vk.Dockerfile` | vkbottle, Bots Long Poll API | Второй активный в проде мессенджер-канал, см. §7.1. |
| `frontend` | `docker/frontend.Dockerfile` | React+TS, статика | Веб-панель (Nginx или Caddy отдаёт статику). |
| `reverse-proxy` | `caddy` (офиц. образ) | Caddy | HTTPS, IP-whitelist панели и API. |

Все процессы (кроме frontend-статики) подключают общий пакет `app/` — единственный источник бизнес-правил.

## 2. Пакет `app/`

```
app/core/        конфигурация (pydantic-settings), permissions.py, security (JWT, argon2), db (engine, WAL pragma, busy_timeout), phone (нормализация), logging, errors
app/models/      SQLAlchemy 2.0 declarative-модели всех сущностей п.6.2 ТЗ
app/repositories/ атомарные операции с БД (пул номеров, платежи) — единственное место с "сырым" SQL/транзакциями
app/services/    бизнес-сценарии: ticket_pool, payment, participant, manual_registration, broadcast, report, audit, panel_user
app/payments/    BasePaymentProvider + RequisitesQrProvider (единственная реализация) + фабрика
app/channels/    BaseMessengerChannel, capability flags, фабрика каналов
```

## 3. Пул номеров и резервирование (п.7.5 ТЗ)

- `TicketPool` материализуется целиком при открытии регистрации розыгрыша: `INSERT` строк `1..max_tickets` со случайным `shuffle_order` (генерируется в Python `random.shuffle`, не полагаемся на `ORDER BY RANDOM()` SQLite).
- Атомарный захват — `app/repositories/ticket_pool_repo.py::reserve_tickets()`:
  1. `BEGIN IMMEDIATE` (через `sqlite3` isolation level / SQLAlchemy `Connection.execution_options(isolation_level="...")` + явный `BEGIN IMMEDIATE`).
  2. `SELECT id FROM ticket_pool WHERE giveaway_id=? AND status='free' ORDER BY shuffle_order LIMIT ? FOR UPDATE`-эквивалент (SQLite не поддерживает `FOR UPDATE` — блокировка обеспечивается самим `BEGIN IMMEDIATE`, который берёт writer-lock на всю БД до конца транзакции).
  3. Если найдено меньше `quantity` — `ROLLBACK`, возвращается фактический остаток, резерв не создаётся ("всё-или-ничего").
  4. Иначе — `UPDATE ... SET status='reserved', reserved_until=?, participant_id=?, payment_id=?/manual_registration_id=? WHERE id IN (...) RETURNING *`, инкремент `Giveaway.tickets_reserved`, `COMMIT`.
- Конкурентные вызовы из разных процессов (backend, channel-telegram) сериализуются через `busy_timeout` на соединении — конкурирующий писатель ждёт снятия блокировки вместо `SQLITE_BUSY`.
- **Провайдеры без резервирования "на лету"** (`BasePaymentProvider.reserves_tickets_on_create=False`, см. §7 — активный по умолчанию `RequisitesQrProvider`) НЕ вызывают `reserve_tickets` в `payment_service.create_payment` вообще: оплата по банковскому переводу подтверждается не мгновенно (может занять несколько дней), поэтому держать резерв пула на это время признано нежелательным (по решению заказчика, см. DECISIONS.md) — участник получает номерки только по факту зачисления денег (см. §4). `create_payment` в этом случае делает только информационную проверку `quantity <= giveaway.free_tickets_count` (без блокировки строк пула — тот же справочный остаток, что и в UI выбора количества), плюс атомарно присваивает `Payment.payment_number` из `Giveaway.next_payment_number` (тот же паттерн инкремента писателя, что и `tickets_reserved`).

## 4. Идемпотентная финализация платежа (п.7.6 ТЗ)

`app/services/payment_service.py::finalize_payment(payment_id, new_status)`:

```sql
BEGIN IMMEDIATE;
UPDATE payment SET status = :new_status, confirmed_at = :now, raw_webhook_payload = :payload
WHERE id = :id AND status = 'PENDING';
-- rowcount == 0 → COMMIT (no-op), уже финализирован
-- rowcount == 1 → выполнить переход пула (issued или free) в той же транзакции → COMMIT
```

Вызывается из двух независимых точек: webhook-роутера банка и фонового planner'а сверки (`check_status`). Оба используют один и тот же метод — гонка исключена атомарностью условного `UPDATE`.

`manual_registration_service` использует тот же паттерн для `confirm`/`cancel`, но с другой семантикой повтора: `finalize_payment` на повторный вызов молча no-op (`applied=False`), а `confirm_manual_registration`/`cancel_manual_registration` вместо этого **поднимают** `ManualRegistrationStateError` — асимметрия намеренная (соответствует формулировке ТЗ), не баг.

**Выдача номерков в момент подтверждения — единый путь для "обычного" и "позднего" случая.** SUCCEEDED-ветка `finalize_payment` сначала пробует `issue_reserved` (резерв, сделанный в `create_payment`, — для провайдеров с `reserves_tickets_on_create=True`). Если резерва нет (`issue_reserved` вернул пусто — единственно возможный случай для провайдеров без резервирования "на лету", см. §3), вызывается общий приватный хелпер `_reserve_and_issue_now` — тот же самый, что обрабатывает и старый edge case "банк подтвердил SUCCEEDED уже ПОСЛЕ того, как платёж был помечен CANCELLED/FAILED и резерв роздан" (переход из `CANCELLED`/`FAILED` в `SUCCEEDED`). Оба случая эквивалентны с точки зрения пула: под платёж нет активного резерва, и нужно захватить `quantity` номеров прямо сейчас, не проверяя `is_locked`/`is_registration_open` розыгрыша (деньги уже списаны). При нехватке номеров — `Payment.oversold=True` (без автовозврата, вне объёма ТЗ §21), тот же сигнал `FinalizeOutcome.late_success_no_tickets`, что и раньше (имя поля не переименовано ради минимального диффа, хотя семантика теперь шире "поздней" оплаты).

## 5. Права доступа (п.11 ТЗ)

- `app/core/permissions.py` — перечень именованных разрешений (`Permission` enum) и матрица `ROLE_PERMISSIONS: dict[PanelRole, set[Permission]]`, точное отражение таблицы п.11.3.
- FastAPI-зависимость `require_permission(permission)` — извлекает роль из JWT, проверяет вхождение в матрицу, иначе `403`.
- Frontend дополнительно скрывает пункты меню (UX), но это не защита — сервер проверяет права всегда.
- Каждое значимое мутирующее действие пишет запись в `AuditLog` через `app/services/audit_service.py` — не обходить это при добавлении новых мутирующих эндпоинтов/хендлеров.

## 6. Идентификация и мультиканальность (п.7.1, 10 ТЗ)

- Единый ключ — `Participant.phone` (нормализованный `app/core/phone.py`).
- `ChannelBinding(channel, external_user_id)` — уникальная пара, привязка только по подтверждённому номеру (`phone_verified=true`) либо при включённом `PlatformSettings.ignore_phone_verification`.
- `app/services/participant_service.py` инкапсулирует всю логику идентификации (find-or-create, приоритет подтверждённого номера, подарочные покупки на неподтверждённый номер).
- `ignore_phone_verification` (тумблер Super Admin) должен оставаться обратимым: `can_access_own_account()` вычисляет `phone_verified OR ignore_phone_verification` каждый раз в момент обращения, а не снимает снимок в момент привязки — выключение флага должно немедленно отозвать доступ у привязок, у которых он держался только флагом (п.7.1/10.3 ТЗ).
- `ChannelBinding.messages_allowed` (`bool | None`) — отзываемое разрешение канала писать участнику первым, там где это применимо (VK: `message_allow`/`message_deny`, см. §7.1); `NULL` для каналов, где право неявно и не отзывается (Telegram).

## 7. Каналы и провайдеры — расширяемость (п.5.4 ТЗ)

- `BaseMessengerChannel` (app/channels/base.py) — абстрактный интерфейс + `ChannelCapabilities` (флаги).
- `BasePaymentProvider` (app/payments/base.py) — абстрактный интерфейс. Интернет-эквайринг (`TBankProvider`/`VTBProvider`/`MockProvider`) полностью удалён по прямому запросу заказчика (см. DECISIONS.md №44) — `RequisitesQrProvider` единственная реализация, панель Super Admin больше не переключает провайдера (переключать не между чем). `MOCK`/`TBANK`/`VTB` остаются значениями `PaymentProviderType` только ради уже существующих исторических строк `Payment.provider` в БД.
- Фабрика каналов (`app/channels/factory.py`) регистрирует реализации по конфигурации; добавление нового канала не требует правок сервисного слоя. Фабрика платежей (`app/payments/factory.py`) сейчас без выбора — `create_provider` конструирует только `RequisitesQrProvider`, для остальных значений `PaymentProviderType` поднимает `ValueError`.
- Рассылки (`broadcast_service`) идут только через Telegram (продуктовое решение). Реактивная доставка (ответ в чат покупателя, напр. `_deliver_tickets` в каждом `channels/*/handlers.py`) идёт через тот канал, которым участник совершал покупку; проактивные уведомления backend (webhook банка, фоновая сверка) уходят через `app/services/notification_service.py::_resolve_notify_targets` во ВСЕ подходящие привязки получателя одновременно (Telegram и VK, если обе есть) — VK участвует, только если участник явно разрешил сообщения от сообщества (см. §7.1, DECISIONS.md №43, отменяет "один канал" из №33).

### 7.1. VK-адаптер (`channels/vk/`)

Реализован на `vkbottle` (Bots Long Poll API, без Callback API/вебхука — та же топология "канал = отдельный процесс", что и у `channel-telegram`); активен в проде (`ACTIVE_CHANNELS`, см. DECISIONS.md #32/#33). Структура `channels/vk/` (`dispatcher.py`/`handlers.py`/`state.py`/`main.py`) параллельна `channels/telegram/`.

- `ChannelCapabilities.can_initiate_dialog=True`, но право отзываемо: VK `message_allow`/`message_deny` события пишутся в `ChannelBinding.messages_allowed` (см. §6) — в отличие от Telegram, это не разовый флаг, а отслеживаемое состояние, которое проверяется перед каждой проактивной отправкой.
- `supports_verified_phone=False` — только `ignore_phone_verification` + ручной ввод номера.
- Медиа — upload-флоу VK (`photos.getMessagesUploadServer`/`saveMessagesPhoto` через `PhotoMessageUploader`), кэш attachment-строки в существующем `Giveaway.poster_media_cache`.
- Инлайн-кнопки — VK `Keyboard(inline=True)`; нажатия приходят отдельным типом события `message_event` (не обычным сообщением), обрабатываются одним диспетчером `_dispatch_message_event` по `payload["a"]` и подтверждаются через `messages.sendMessageEventAnswer` (аналог `callback.answer()` в Telegram).
- Деплой — процесс `channel-vk` (`docker/vk.Dockerfile`), extras `vk`. Backend дополнительно поднимает свой outbound-only `VkChannel` (без polling, `backend/main.py::lifespan`) для проактивных уведомлений — тем же способом, что и `TelegramChannel` (DECISIONS.md #24) — поэтому `docker/backend.Dockerfile` тоже ставит `[vk]`-зависимости (см. DECISIONS.md #25 про инцидент с забытыми зависимостями канала в образе backend).

### 7.2. `RequisitesQrProvider` — оплата по QR с банковскими реквизитами (единственный провайдер)

`app/payments/requisites_qr.py` — единственный способ приёма оплаты (интернет-эквайринг удалён, см. DECISIONS.md №44):

- `create_payment` — без сети: собирает QR-payload по ГОСТ Р 56042-2014 (формат ST00012, `app/payments/qr_requisites.py::build_st00012_payload`) из реквизитов получателя (`.env`, `REQUISITES_*`) и назначения платежа `"Оплата по счету № {PREFIX}-{NNNNN} от {дата}, в т.ч. НДС..."` (номер счёта — `Giveaway.format_invoice_number`, ставка НДС — `.env REQUISITES_VAT_RATE_PERCENT`). QR рендерится в канале (`channels/*/channel.py::send_qr_code`) как байты **Windows-1251** — общепринятая практика для банковских QR-сканеров (риск для проверки на реальных приложениях, см. DECISIONS.md).
- `reserves_tickets_on_create=False` (см. §3) — нет резерва при создании, `payment_url=None` (нет ссылки на оплату, только QR).
- `verify_and_parse_webhook` не поддерживается (нет вебхука у этого провайдера).
- `check_status` — разовая сверка по запросу участника: ищет совпадение по номеру счёта в свежей выписке (та же логика, что и в фоновой сверке ниже), не полагаясь на ожидание следующего тика.
- `cancel` — no-op (`CANCELLED` без сетевого вызова, нет банковской сессии для закрытия). Статический QR технически может быть оплачен и после отмены счёта в боте — это не теряется: фоновая сверка найдёт деньги и обработает их через `_reserve_and_issue_now` (см. §4), как и любую "позднюю" оплату.

**Подтверждение оплаты — сверка выписки расчётного счёта, не вебхук.** `app/services/bank_reconciliation_service.py::reconcile()` (вызывается фоновым циклом `backend/background/__init__.py::run_background_loop` наравне с `_reconcile_pending_payments`, но независимо от него — см. §3): раз в тик забирает входящие операции по счёту за скользящее окно (`BANK_STATEMENT_LOOKBACK_DAYS`) через `BaseBankStatementProvider` (`app/payments/bank_statement.py`, реализация — `TBankStatementProvider` поверх Т-Банк T-API `GET /api/v1/statement`, по документации — тот же принятый на старте риск, что и у интернет-эквайринга до его удаления, см. DECISIONS.md №1/№40) и сопоставляет их с неоплаченными `Payment(provider=REQUISITES_QR)` **по назначению платежа** (префикс розыгрыша + номер счёта, `Giveaway.prefix` уникален глобально) **И точному совпадению суммы** с `Payment.amount` (см. DECISIONS.md №38 — отменяет более раннее решение "без сверки суммы"). Операция с совпавшим назначением, но другой суммой (неполная либо избыточная оплата), счёт не закрывает — остаётся `PENDING`, расхождение логируется (`bank_statement_amount_mismatch`) и сохраняется на `Payment.amount_mismatch`/`amount_mismatch_bank_amount` для подсветки в панели «Продажи» (строка + бейдж с фактической суммой, см. DECISIONS.md №39); пока флаг активен, TTL-автопросрочка для этого счёта пропускается. Найденные совпадения (назначение + сумма) финализируются через обычный `payment_service.finalize_payment(SUCCEEDED)`. Неоплаченные счета старше `REQUISITES_INVOICE_TTL_DAYS` (и без активного расхождения суммы) помечаются `FAILED` (резерва нет — освобождать нечего, только снимается блокировка "одна активная покупка").

**Квитанции.** Участник присылает в бот фото/документ квитанции ПОСЛЕ оплаты — `app/services/receipt_service.py::save_receipt` сохраняет файл на диск (`RECEIPTS_DIR`, тот же bind-mount паттерн `./data/`, что и БД) и создаёт `PaymentReceipt`, привязанную к последнему `PENDING`-платежу участника. Не распознаётся — только хранится, просматривается оператором в панели («Продажи» → колонка «Квитанция»).

## 8. Инфраструктура

- SQLite в режиме WAL, единый файл `DATABASE_PATH`, все процессы открывают соединение с `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=<мс>`.
- Все чувствительные рантайм-данные (БД, бэкапы, квитанции участников, TLS-сертификаты/ACME-аккаунт Caddy) — bind mount в `./data/` внутри репозитория (не именованные Docker volumes), ради переносимости: копия каталога проекта содержит всё состояние для запуска на другом хосте (см. DECISIONS.md #28). `./data/` в `.gitignore`. Квитанции (`RECEIPTS_DIR=/data/receipts`) смонтированы во все три процесса, пишущих/читающих их напрямую через `app/` (backend, channel-telegram, channel-vk) — тот же принцип, что и для `DATABASE_PATH`.
- Caddy: HTTPS (ACME или self-signed для dev), IP-whitelist для панели (`frontend`/`backend` API), проксирование только webhook-эндпоинтов банков наружу, `/metrics` не проксируется.
- `scripts/backup_db.sh` — `sqlite3 $DB "VACUUM INTO '$BACKUP'"`, gzip, ротация по `BACKUP_RETENTION_DAYS`.
- `scripts/deploy.sh` — `git pull`, `docker compose build`, `docker compose up -d`, ожидание healthcheck.

## 9. Тестирование

- `tests/` — pytest, всегда файловый temp-файл SQLite (`tests/conftest.py`), никогда `sqlite:///:memory:` — in-memory даёт каждому соединению свою БД под `SingletonThreadPool`, что скрывает гонки писателей в конкурентных тестах пула номеров.
- Группы тестов соответствуют п.20.1 ТЗ один-в-один (см. `README.md` → «Тестирование»).
