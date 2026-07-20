#!/bin/sh
# Обновление системы одной командой (п.19 ТЗ): git pull, пересборка образов,
# перезапуск с ожиданием healthcheck. Миграции применяются автоматически
# при старте backend (см. scripts/backend-entrypoint.sh).
#
# ТОЛЬКО для прода — явно исключает docker-compose.override.yml (self-signed
# TLS + открытый наружу backend:8000 для локальной разработки). Без -f сюда
# бы неявно подмешался override.yml на любом хосте, где лежит его файл (он
# закоммичен в репозиторий) — именно так один раз погас боевой HTTPS.
set -e

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.yml"

echo "==> git pull"
git pull --ff-only

echo "==> docker compose build"
$COMPOSE build

echo "==> docker compose up -d"
$COMPOSE up -d

echo "==> ожидание healthcheck backend..."
for _ in $(seq 1 30); do
    status="$($COMPOSE ps --format json backend 2>/dev/null | grep -o '"Health":"[a-z]*"' | cut -d'"' -f4)"
    if [ "$status" = "healthy" ]; then
        echo "backend healthy"
        exit 0
    fi
    sleep 2
done

echo "ВНИМАНИЕ: backend не стал healthy за отведённое время — проверьте 'docker compose -f docker-compose.yml logs backend'" >&2
exit 1
