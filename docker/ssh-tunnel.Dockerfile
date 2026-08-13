# syntax=docker/dockerfile:1.7
# ssh-tunnel — sidecar-контейнер, поднимает динамический SOCKS5-форвардинг
# (`ssh -D`) через SSH на удалённый хост, авторизация по ключу. Заменяет
# сторонний HTTP/SOCKS-прокси для channel-telegram (см. DECISIONS_LOG.md №71,
# ARCHITECTURE.md §8): channel-telegram подключается к api.telegram.org через
# socks5://ssh-tunnel:<port>, TCP-туннелирован по SSH до удалённого хоста —
# TLS/SNI к api.telegram.org при этом не трогается, только транспорт.
FROM alpine:3.20

RUN apk add --no-cache openssh-client autossh netcat-openbsd

COPY docker/ssh-tunnel-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
