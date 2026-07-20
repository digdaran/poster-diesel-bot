# Backend (FastAPI) — REST API панели, webhook банков, фоновые задачи, /metrics.
# Отдельный образ со своими зависимостями (requirements/backend.txt), общий пакет
# app/ подключается без изменений (п.5.1, 5.3 ТЗ). Также подключает channels/telegram/
# (пакет + [telegram]-зависимости) — backend поднимает собственный outbound-only
# TelegramChannel для проактивных уведомлений об исходе платежа (см. DECISIONS.md,
# app/services/notification_service.py); polling здесь не запускается, тем не менее
# aiogram.Bot нужен для send_message/send_media.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY backend ./backend
COPY channels ./channels
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir ".[backend,telegram]"

COPY scripts/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fs http://localhost:8000/health || exit 1

ENTRYPOINT ["backend-entrypoint.sh"]
