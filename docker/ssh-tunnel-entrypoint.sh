#!/bin/sh
# Поднимает динамический SOCKS5-форвардинг (`ssh -D`) на удалённый хост через
# autossh (авто-реконнект при обрыве соединения). См. docker-compose.yml
# (сервис ssh-tunnel), .env.example (SSH_TUNNEL_*) и DECISIONS_LOG.md №71.
#
# Приватный ключ и known_hosts монтируются read-only из ./data/ssh/ (хост) —
# см. README.md, раздел деплоя, как их сгенерировать и как установить
# публичный ключ в authorized_keys на удалённом сервере.
set -eu

: "${SSH_TUNNEL_HOST:?SSH_TUNNEL_HOST не задан (см. .env.example)}"
: "${SSH_TUNNEL_USER:?SSH_TUNNEL_USER не задан (см. .env.example)}"
SSH_TUNNEL_PORT="${SSH_TUNNEL_PORT:-22}"
SSH_TUNNEL_LOCAL_PORT="${SSH_TUNNEL_LOCAL_PORT:-1080}"
# Фиксированные пути внутри контейнера — см. docker-compose.yml (volumes сервиса
# ssh-tunnel монтируют SSH_TUNNEL_PRIVATE_KEY_PATH/SSH_TUNNEL_KNOWN_HOSTS_PATH
# с хоста сюда же).
PRIVATE_KEY=/run/secrets/ssh_tunnel_key
KNOWN_HOSTS=/run/secrets/known_hosts

[ -f "$PRIVATE_KEY" ] || {
  echo "Приватный ключ не найден: $PRIVATE_KEY (сгенерируйте data/ssh/telegram_tunnel_ed25519, см. README.md)" >&2
  exit 1
}
[ -f "$KNOWN_HOSTS" ] || {
  echo "known_hosts не найден: $KNOWN_HOSTS (ssh-keyscan -p \$SSH_TUNNEL_PORT \$SSH_TUNNEL_HOST, см. README.md)" >&2
  exit 1
}

# Ключ должен быть доступен только владельцу — иначе ssh откажется его читать.
chmod 600 "$PRIVATE_KEY" 2>/dev/null || true

exec autossh -M 0 -N \
  -D "0.0.0.0:${SSH_TUNNEL_LOCAL_PORT}" \
  -i "$PRIVATE_KEY" \
  -p "$SSH_TUNNEL_PORT" \
  -o "UserKnownHostsFile=${KNOWN_HOSTS}" \
  -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  "${SSH_TUNNEL_USER}@${SSH_TUNNEL_HOST}"
