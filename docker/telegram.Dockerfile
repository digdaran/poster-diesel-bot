# channel-telegram — aiogram 3, long polling. Отдельный образ/процесс со своими
# зависимостями (requirements/telegram.txt); общий пакет app/ — без изменений
# (п.5.1, 5.3, 10.4 ТЗ).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY channels ./channels

RUN pip install --no-cache-dir ".[telegram]"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "channels.telegram.main" || exit 1

CMD ["python", "-m", "channels.telegram.main"]
