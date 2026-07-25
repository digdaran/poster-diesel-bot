# Платформа розыгрышей цифровых постеров

Production-ready система продажи цифровых/физических постеров и розыгрышей номерков.
Реализовано строго по `ТЗ_Raffle_Platform.md` (см. также `ARCHITECTURE.md` и `DECISIONS.md`).

> Статус: в разработке (см. прогресс по этапам в PR этого репозитория).

## Стек

Python 3.11+ / FastAPI / aiogram 3 (Telegram, long polling) / React + TypeScript / SQLite (WAL) / Docker + docker-compose / Caddy / Prometheus.

## Быстрый старт (dev)

```bash
cp .env.example .env
# отредактируйте .env: SUPERADMIN_LOGIN/PASSWORD, JWT_SECRET, TELEGRAM_BOT_TOKEN (для боевого запуска бота)
docker compose up --build
```

- Панель: https://localhost (или домен из `PANEL_DOMAIN`), доступ только с IP из `PANEL_IP_WHITELIST`.
- Backend API: за reverse-proxy, доступ только с IP из `PANEL_IP_WHITELIST` (банковских webhook-эндпоинтов нет — оплата подтверждается сверкой выписки, см. `DECISIONS.md`).
- Первый вход — `SUPERADMIN_LOGIN` / `SUPERADMIN_PASSWORD` из `.env` (бутстрап при пустой таблице `PanelUser`).

## Локальная разработка без Docker

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,telegram,dev]"
alembic upgrade head
uvicorn backend.main:app --reload
```

Telegram-канал отдельным процессом:

```bash
python -m channels.telegram.main
```

## Тестирование

```bash
pip install -e ".[dev]"
pytest
ruff check .
black --check .
mypy app backend channels
```

Тесты используют изолированную временную файловую SQLite (никогда `:memory:` — под
`SingletonThreadPool` in-memory даёт каждому соединению свою БД и скрывает гонки писателей
в конкурентных тестах пула номеров, см. `ARCHITECTURE.md` §9), без Docker и сети. Обязательные группы (п.20.1 ТЗ):

- идемпотентность финализации платежа (повторная сверка выписки, расхождение суммы, неизвестный заказ, отказ);
- пул и резервирование (атомарный захват, гонка «на хвосте» тиража, TTL, остановка/возобновление продаж);
- матрица прав ролей (403 на недоступных эндпоинтах);
- ручные регистрации (подтверждение, запрет повторного подтверждения, отмена только до подтверждения);
- идентификация и привязка номера (только по подтверждённому номеру, подарочные покупки, приоритет подтверждённого);
- переключатель `ignore_phone_verification`;
- мультиканальность (`ChannelBinding`, единый участник по телефону);
- уведомления и рассылки (транзакционные — в канал покупки; массовые — только Telegram).

## Развёртывание и эксплуатация

### Продакшен (домен + реальный HTTPS)

```bash
cp .env.example .env
# заполните: PANEL_DOMAIN, ACME_EMAIL, PANEL_IP_WHITELIST, JWT_SECRET,
# SUPERADMIN_LOGIN/PASSWORD, TELEGRAM_BOT_TOKEN/VK_GROUP_TOKEN, REQUISITES_*
# (реквизиты для QR), TBANK_STATEMENT_* (сверка выписки)
docker compose -f docker-compose.yml up -d --build
```

Caddy сам получит сертификат Let's Encrypt для `PANEL_DOMAIN`. Наружу без IP-ограничения
ничего не публикуется — панель (`/`, `/api/*`) доступна только с IP из
`PANEL_IP_WHITELIST`, банковских webhook-эндпоинтов нет (оплата подтверждается
сверкой выписки, не входящим запросом от банка). `/metrics` не проксируется вообще.

### Локальная разработка (self-signed, без домена)

`docker-compose.override.yml` подключается автоматически при обычном
`docker compose up` и включает `Caddyfile.dev` (self-signed TLS) + прямой порт
`8000` для отладки backend.

```bash
cp .env.example .env
docker compose up --build
```

### Хранение данных и перенос на другой хост

Все чувствительные рантайм-данные — БД SQLite (`raffle.db` + WAL), снимки `backup_db.sh`
и TLS-сертификаты/ACME-аккаунт Caddy — смонтированы bind mount'ом в `./data/` внутри
каталога проекта (не в именованных Docker volumes), чтобы состояние переносилось вместе
с копией репозитория:

```
data/
├── db/        # raffle.db, raffle.db-wal, raffle.db-shm  (DATABASE_PATH внутри контейнера: /data/raffle.db)
├── backups/   # снимки scripts/backup_db.sh
└── caddy/
    ├── data/    # сертификаты Let's Encrypt / ACME-аккаунт
    └── config/  # состояние Caddy (autosave.json)
```

`./data/` в `.gitignore` — данные не коммитятся в git, но переносятся вместе с копией
каталога проекта (`rsync`/`tar`/scp и т.п.). Чтобы поднять систему в том же состоянии на
другом хосте: остановить стек (`docker compose down`), скопировать весь каталог проекта
(включая `data/` и `.env`) на новый хост, запустить `docker compose up -d --build`.

### Обновление и резервное копирование

- `scripts/deploy.sh` — `git pull` + пересборка образов + `docker compose up -d`
  с ожиданием healthcheck backend. Миграции применяются автоматически при старте
  backend-контейнера (`scripts/backend-entrypoint.sh`).
- `scripts/backup_db.sh` — консистентный снимок SQLite (`VACUUM INTO` через
  `app/core/backup.py`), gzip, ротация по `BACKUP_RETENTION_DAYS`. Добавьте в cron
  хоста: `0 3 * * * cd /opt/raffle-platform && ./scripts/backup_db.sh`.
- **CI не используется** (сознательное решение заказчика). Перед каждым пушем в
  `main` вручную прогоняются `ruff check`, `black --check`, `mypy`, `pytest` —
  все должны быть зелёными.

> Примечание: `docker compose build` не прогонялся в песочнице агента (нет
> Docker) — см. DECISIONS.md, п.16. Сборка и полная валидация docker-compose
> стека выполняется на стороне заказчика после установки Docker.

## Известные ограничения первой версии (см. п.21 ТЗ и `DECISIONS.md`)

Не реализуются в этой версии: готовые адаптеры VK/MAX (только интерфейс/заглушки), второй банк без реальных боевых ключей (заготовка по документации), возврат платежей, отмена подтверждённых ручных регистраций, автоматический выбор победителей, масштабирование пула на миллионы номеров.
