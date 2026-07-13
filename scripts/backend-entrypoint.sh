#!/bin/sh
# Точка входа backend-контейнера: применяет аддитивные Alembic-миграции перед
# стартом (безопасно при повторных запусках — no-op, если БД уже на head),
# затем запускает Uvicorn. Бутстрап первого Super Admin выполняется самим
# приложением при старте (см. backend/main.py::lifespan, п.12 ТЗ).
set -e

echo "[backend-entrypoint] applying migrations..."
ALEMBIC_DATABASE_URL="sqlite:///${DATABASE_PATH:-/data/raffle.db}" alembic upgrade head

echo "[backend-entrypoint] starting uvicorn..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
