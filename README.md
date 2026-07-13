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
- Backend API: за reverse-proxy, наружу — только webhook-эндпоинты банков.
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

Тесты используют изолированную in-memory SQLite, без Docker и сети. Обязательные группы (п.20.1 ТЗ):

- идемпотентность финализации платежа (повторный webhook, неверная подпись, неизвестный заказ, отказ);
- пул и резервирование (атомарный захват, гонка «на хвосте» тиража, TTL, остановка/возобновление продаж);
- матрица прав ролей (403 на недоступных эндпоинтах);
- ручные регистрации (подтверждение, запрет повторного подтверждения, отмена только до подтверждения);
- идентификация и привязка номера (только по подтверждённому номеру, подарочные покупки, приоритет подтверждённого);
- переключатель `ignore_phone_verification`;
- мультиканальность (`ChannelBinding`, единый участник по телефону);
- уведомления и рассылки (транзакционные — в канал покупки; массовые — только Telegram).

## Развёртывание и эксплуатация

- `scripts/deploy.sh` — обновление одной командой.
- `scripts/backup_db.sh` — резервное копирование SQLite (`VACUUM INTO`), сжатие, ротация.
- CI (GitHub Actions, `.github/workflows/ci.yml`) — lint, mypy, pytest на каждый push/PR.

## Известные ограничения первой версии (см. п.21 ТЗ и `DECISIONS.md`)

Не реализуются в этой версии: готовые адаптеры VK/MAX (только интерфейс/заглушки), второй банк без реальных боевых ключей (заготовка по документации), возврат платежей, отмена подтверждённых ручных регистраций, автоматический выбор победителей, масштабирование пула на миллионы номеров.
