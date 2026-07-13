#!/bin/sh
# Обновление системы одной командой (п.19 ТЗ): git pull, пересборка образов,
# перезапуск с ожиданием healthcheck. Миграции применяются автоматически
# при старте backend (см. scripts/backend-entrypoint.sh).
set -e

cd "$(dirname "$0")/.."

echo "==> git pull"
git pull --ff-only

echo "==> docker compose build"
docker compose build

echo "==> docker compose up -d"
docker compose up -d

echo "==> ожидание healthcheck backend..."
for _ in $(seq 1 30); do
    status="$(docker compose ps --format json backend 2>/dev/null | grep -o '"Health":"[a-z]*"' | cut -d'"' -f4)"
    if [ "$status" = "healthy" ]; then
        echo "backend healthy"
        exit 0
    fi
    sleep 2
done

echo "ВНИМАНИЕ: backend не стал healthy за отведённое время — проверьте 'docker compose logs backend'" >&2
exit 1
