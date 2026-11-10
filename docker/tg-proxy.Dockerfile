# syntax=docker/dockerfile:1.7
# tg-proxy — xray-клиент: SOCKS5 на :1080 внутри docker-сети, наружу через
# несколько EU-узлов remnanode (VLESS+Reality) с автоматическим выбором живого
# (balancer + observatory). Резерв — старый ssh-tunnel. См. DECISIONS_LOG.md №94,
# ARCHITECTURE.md §8, шаблон конфига docs/xray-config.example.json.
#
# Бинарник берём из официального образа XTLS (сам он distroless, без curl для
# healthcheck), а запускаем на Alpine.
FROM ghcr.io/xtls/xray-core:26.10.31 AS xray

FROM alpine:3.20

RUN apk add --no-cache ca-certificates curl

COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=xray /usr/local/share/xray/ /usr/local/share/xray/

# Конфиг с UUID/ключами узлов монтируется read-only из ./data/xray/config.json.
ENTRYPOINT ["/usr/local/bin/xray", "run", "-c", "/etc/xray/config.json"]
