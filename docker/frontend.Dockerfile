# frontend — сборка React+TS SPA, раздаётся статикой (п.5.1, 11.1 ТЗ). Реальный
# HTTPS/маршрутизация — на внешнем reverse-proxy (Caddy, отдельный сервис),
# этот контейнер только отдаёт собранную статику по HTTP внутри docker-сети.
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine AS serve
COPY --from=build /app/dist /srv
COPY docker/frontend.Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=5 \
    CMD wget -qO- http://localhost:80 >/dev/null 2>&1 || exit 1
