# syntax=docker/dockerfile:1.7
# Backend (FastAPI) — REST API панели, фоновые задачи (сверка платежей по
# банковской выписке, освобождение просроченных резервов), /metrics. Банковских
# webhook-роутеров больше нет — интернет-эквайринг удалён, см. DECISIONS_LOG.md #44.
# Отдельный образ со своими зависимостями (requirements/backend.txt), общий пакет
# app/ подключается без изменений (п.5.1, 5.3 ТЗ). Также подключает channels/telegram/
# и channels/vk/ (пакеты + [telegram]/[vk]-зависимости) — backend поднимает
# собственные outbound-only TelegramChannel/VkChannel для проактивных уведомлений
# об исходе платежа (см. DECISIONS_LOG.md #24, #33, app/services/notification_service.py);
# polling здесь не запускается, тем не менее aiogram.Bot/vkbottle.Bot нужны для
# send_message/send_media/deliver_purchase. Оба набора зависимостей обязательны
# независимо от того, какой канал реально активен (ACTIVE_CHANNELS) — backend
# импортирует оба класса на уровне модуля (backend/api/deps.py), их отсутствие в
# образе роняет процесс в рестарт-цикл (боевой инцидент, см. DECISIONS_LOG.md #25).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[backend,telegram,vk]"

COPY scripts/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fs http://localhost:8000/health || exit 1

ENTRYPOINT ["backend-entrypoint.sh"]
