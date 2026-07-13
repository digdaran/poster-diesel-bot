#!/bin/sh
# Резервное копирование БД: консистентный снимок (VACUUM INTO) + gzip + ротация
# по BACKUP_RETENTION_DAYS (п.18.1, 19 ТЗ). Выполняется внутри backend-контейнера,
# где смонтированы одновременно /data (БД) и /backups (снимки).
#
# Использование: ./scripts/backup_db.sh   (запускать из корня репозитория; можно
# добавить в cron хоста, напр. "0 3 * * * cd /opt/raffle-platform && ./scripts/backup_db.sh")
set -e

cd "$(dirname "$0")/.."
docker compose exec -T backend python -m app.core.backup
