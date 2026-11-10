"""Доступность Telegram Bot API с точки зрения backend — для плашки
«Telegram недоступен» в панели (DECISIONS_LOG.md №94).

С 2026-11-08 прод сутки не мог достучаться до Telegram (заблокировали IP
выходного SSH-сервера), а в панели этого не было видно: сообщения молча
копились в очереди доставки. Теперь backend раз в минуту делает `getMe` через
тот же прокси, что и все отправки, и отдаёт результат панели
(`GET /api/system/channel-health`).

Состояние хранится в памяти процесса backend: после рестарта первая проверка
проходит сразу, а до неё статус неизвестен (`ok is None`)."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable

import structlog

from app.models.base import utcnow

logger = structlog.get_logger(__name__)

CHECK_INTERVAL_SEC = 60
CHECK_TIMEOUT_SEC = 15


class ChannelHealth:
    def __init__(self) -> None:
        self.ok: bool | None = None
        self.failing_since: dt.datetime | None = None
        self.last_checked_at: dt.datetime | None = None
        self.last_error: str | None = None

    def record_success(self, now: dt.datetime) -> None:
        if self.failing_since is not None:
            logger.info("channel_health_recovered", failing_since=self.failing_since.isoformat())
        self.ok = True
        self.failing_since = None
        self.last_error = None
        self.last_checked_at = now

    def record_failure(self, now: dt.datetime, error: str) -> None:
        if self.failing_since is None:
            self.failing_since = now
            logger.warning("channel_health_failing", error=error)
        self.ok = False
        self.last_error = error
        self.last_checked_at = now


async def check(health: ChannelHealth, probe: Callable[[], Awaitable[object]]) -> None:
    """Одна проверка: `probe` — сетевой вызов канала (для Telegram — `bot.get_me`).
    Любая ошибка или таймаут считаются недоступностью, наружу не пробрасываются."""
    try:
        await asyncio.wait_for(probe(), timeout=CHECK_TIMEOUT_SEC)
    except Exception as exc:  # любой сбой транспорта = канал недоступен
        health.record_failure(utcnow(), f"{type(exc).__name__}: {exc}"[:300])
    else:
        health.record_success(utcnow())


async def run_monitor(health: ChannelHealth, probe: Callable[[], Awaitable[object]]) -> None:
    """Работает, пока не отменена (backend/main.py::lifespan)."""
    while True:
        await check(health, probe)
        await asyncio.sleep(CHECK_INTERVAL_SEC)
