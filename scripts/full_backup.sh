#!/bin/sh
# Полное офсайт-резервное копирование рантайм-данных проекта: свежий консистентный
# снимок SQLite (переиспользует app/core/backup.py, см. scripts/backup_db.sh) +
# data/receipts, data/posters, data/caddy, .env — через restic (дедупликация +
# шифрование) в локальный репозиторий (хранится RESTIC_LOCAL_KEEP_WITHIN, по
# умолчанию 1 день) и в офсайт-репозиторий на другом сервере по SSH/SFTP
# (RESTIC_REMOTE_REPO).
#
# Исходный код в бэкап не входит — он уже в git и воспроизводится из образов.
#
# Требования на хосте:
#   - установленный restic (https://restic.net)
#   - файл с паролем шифрования репозиториев: RESTIC_PASSWORD_FILE (chmod 600,
#     ХРАНИТЬ ВНЕ каталога проекта, не коммитить)
#   - passwordless SSH-доступ (ключ) с этого хоста до RESTIC_REMOTE_REPO
#
# Настройка — переменные окружения (см. .env.example, секция RESTIC_*):
#   RESTIC_LOCAL_REPO, RESTIC_REMOTE_REPO, RESTIC_PASSWORD_FILE,
#   RESTIC_LOCAL_KEEP_WITHIN (по умолчанию 1d),
#   RESTIC_REMOTE_KEEP_HOURLY / RESTIC_REMOTE_KEEP_DAILY (по умолчанию 24 / 30)
#
# Использование: ./scripts/full_backup.sh   (запускать из корня репозитория; добавить
# в cron хоста для ежечасного запуска, напр.:
#   0 * * * * cd /opt/raffle-platform && ./scripts/full_backup.sh >> /var/log/raffle-full-backup.log 2>&1
set -eu

cd "$(dirname "$0")/.."

# .env — простой KEY=VALUE (как читают его docker compose/pydantic-settings), НЕ
# валидный POSIX shell: значения (например REQUISITES_RECIPIENT_NAME) могут содержать
# пробелы без кавычек. Поэтому не source'им файл целиком, а точечно вытаскиваем только
# нужные переменные, если они не заданы окружением снаружи.
env_get() {
  [ -f .env ] || return 0
  grep -m1 "^$1=" .env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

RESTIC_LOCAL_REPO="${RESTIC_LOCAL_REPO:-$(env_get RESTIC_LOCAL_REPO)}"
RESTIC_REMOTE_REPO="${RESTIC_REMOTE_REPO:-$(env_get RESTIC_REMOTE_REPO)}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$(env_get RESTIC_PASSWORD_FILE)}"
RESTIC_LOCAL_KEEP_WITHIN="${RESTIC_LOCAL_KEEP_WITHIN:-$(env_get RESTIC_LOCAL_KEEP_WITHIN)}"
RESTIC_REMOTE_KEEP_HOURLY="${RESTIC_REMOTE_KEEP_HOURLY:-$(env_get RESTIC_REMOTE_KEEP_HOURLY)}"
RESTIC_REMOTE_KEEP_DAILY="${RESTIC_REMOTE_KEEP_DAILY:-$(env_get RESTIC_REMOTE_KEEP_DAILY)}"

: "${RESTIC_LOCAL_REPO:?RESTIC_LOCAL_REPO не задан (см. .env.example)}"
: "${RESTIC_REMOTE_REPO:?RESTIC_REMOTE_REPO не задан (см. .env.example)}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE не задан (см. .env.example)}"
RESTIC_LOCAL_KEEP_WITHIN="${RESTIC_LOCAL_KEEP_WITHIN:-1d}"
RESTIC_REMOTE_KEEP_HOURLY="${RESTIC_REMOTE_KEEP_HOURLY:-24}"
RESTIC_REMOTE_KEEP_DAILY="${RESTIC_REMOTE_KEEP_DAILY:-30}"

export RESTIC_PASSWORD_FILE

# Свежий консистентный снимок SQLite (VACUUM INTO) перед копированием — без этого
# restic читал бы raffle.db/-wal/-shm "вживую", без гарантии консистентности снимка.
docker compose exec -T backend python -m app.core.backup

BACKUP_PATHS="data/backups data/receipts data/posters data/caddy .env"

echo "== Локальный репозиторий: $RESTIC_LOCAL_REPO =="
if [ ! -d "$RESTIC_LOCAL_REPO" ]; then
  restic -r "$RESTIC_LOCAL_REPO" init
fi
# shellcheck disable=SC2086
restic -r "$RESTIC_LOCAL_REPO" backup $BACKUP_PATHS --tag hourly
restic -r "$RESTIC_LOCAL_REPO" forget --keep-within "$RESTIC_LOCAL_KEEP_WITHIN" --prune

echo "== Офсайт-репозиторий: $RESTIC_REMOTE_REPO =="
if ! restic -r "$RESTIC_REMOTE_REPO" snapshots >/dev/null 2>&1; then
  restic -r "$RESTIC_REMOTE_REPO" init
fi
# shellcheck disable=SC2086
restic -r "$RESTIC_REMOTE_REPO" backup $BACKUP_PATHS --tag hourly
restic -r "$RESTIC_REMOTE_REPO" forget \
  --keep-hourly "$RESTIC_REMOTE_KEEP_HOURLY" \
  --keep-daily "$RESTIC_REMOTE_KEEP_DAILY" \
  --prune
