# ARCHITECTURE.md — архитектура платформы розыгрышей

> Источник требований — `ТЗ_Raffle_Platform.md`. Здесь описывается, как ТЗ реализовано технически.

## 1. Процессы

| Процесс | Образ | Технология | Роль |
|---|---|---|---|
| `backend` | `docker/backend.Dockerfile` | FastAPI + Uvicorn | REST API панели, webhook-роутеры банков, фоновые задачи (сверка платежей, освобождение просроченных резервов), `/metrics`. |
| `channel-telegram` | `docker/telegram.Dockerfile` | aiogram 3, long polling | Единственный активный в проде мессенджер-канал. |
| `frontend` | `docker/frontend.Dockerfile` | React+TS, статика | Веб-панель (Nginx или Caddy отдаёт статику). |
| `reverse-proxy` | `caddy` (офиц. образ) | Caddy | HTTPS, IP-whitelist панели, маршрутизация только webhook наружу. |

Все процессы (кроме frontend-статики) подключают общий пакет `app/` — единственный источник бизнес-правил.

## 2. Пакет `app/`

```
app/core/        конфигурация (pydantic-settings), permissions.py, security (JWT, argon2), db (engine, WAL pragma, busy_timeout), phone (нормализация), logging, errors
app/models/      SQLAlchemy 2.0 declarative-модели всех сущностей п.6.2 ТЗ
app/repositories/ атомарные операции с БД (пул номеров, платежи) — единственное место с "сырым" SQL/транзакциями
app/services/    бизнес-сценарии: ticket_pool, payment, participant, manual_registration, broadcast, report, audit, panel_user
app/payments/    BasePaymentProvider + MockProvider/TBankProvider/VTBProvider + фабрика
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

## 7. Каналы и провайдеры — расширяемость (п.5.4 ТЗ)

- `BaseMessengerChannel` (app/channels/base.py) — абстрактный интерфейс + `ChannelCapabilities` (флаги).
- `BasePaymentProvider` (app/payments/base.py) — абстрактный интерфейс.
- Обе фабрики (`app/channels/factory.py`, `app/payments/factory.py`) регистрируют реализации по конфигурации; добавление нового канала/банка не требует правок сервисного слоя.
- Рассылки (`broadcast_service`) идут только через Telegram (продуктовое решение); транзакционные уведомления идут через тот канал, которым участник совершал покупку.

## 8. Инфраструктура

- SQLite в режиме WAL, единый файл `DATABASE_PATH`, все процессы открывают соединение с `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=<мс>`.
- Все чувствительные рантайм-данные (БД, бэкапы, TLS-сертификаты/ACME-аккаунт Caddy) — bind mount в `./data/` внутри репозитория (не именованные Docker volumes), ради переносимости: копия каталога проекта содержит всё состояние для запуска на другом хосте (см. DECISIONS.md #28). `./data/` в `.gitignore`.
- Caddy: HTTPS (ACME или self-signed для dev), IP-whitelist для панели (`frontend`/`backend` API), проксирование только webhook-эндпоинтов банков наружу, `/metrics` не проксируется.
- `scripts/backup_db.sh` — `sqlite3 $DB "VACUUM INTO '$BACKUP'"`, gzip, ротация по `BACKUP_RETENTION_DAYS`.
- `scripts/deploy.sh` — `git pull`, `docker compose build`, `docker compose up -d`, ожидание healthcheck.

## 9. Тестирование

- `tests/` — pytest, всегда файловый temp-файл SQLite (`tests/conftest.py`), никогда `sqlite:///:memory:` — in-memory даёт каждому соединению свою БД под `SingletonThreadPool`, что скрывает гонки писателей в конкурентных тестах пула номеров.
- Группы тестов соответствуют п.20.1 ТЗ один-в-один (см. `README.md` → «Тестирование»).
