"""Точка входа FastAPI backend (п.5.1, 19 ТЗ): REST API панели, метрики Prometheus.
Общий пакет app/ переиспользуется без изменений."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from app.core.config import get_settings
from app.core.db import Database
from app.models import Base
from app.services import panel_user_service
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from backend import background
from backend.api import (
    audit,
    auth,
    bank_reconciliation,
    broadcasts,
    dashboard,
    giveaways,
    manual_registrations,
    panel_users,
    participants,
    reports,
    sales,
    tickets,
)
from backend.api import (
    settings as settings_api,
)
from channels.telegram.channel import TelegramChannel
from channels.vk.channel import VkChannel

logger = structlog.get_logger(__name__)

REQUEST_COUNT = Counter("http_requests_total", "Всего HTTP-запросов", ["method", "path", "status"])
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Латентность запроса", ["method", "path"]
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    db = Database(settings)
    Base.metadata.create_all(db.engine)  # безопасный no-op, если таблицы уже созданы миграциями
    with db.session() as session:
        panel_user_service.bootstrap_superadmin(
            session, login=settings.superadmin_login, password=settings.superadmin_password
        )
    app.state.db = db
    app.state.settings = settings

    # Outbound-only инстансы — этот процесс не запускает polling ни для одного
    # канала, они нужны только для проактивных уведомлений об исходе платежа
    # (webhook, фоновая сверка) через app/services/notification_service.py,
    # см. DECISIONS_LOG.md #24, #33.
    telegram_channel = (
        TelegramChannel(
            token=settings.telegram_bot_token, proxy_url=settings.telegram_proxy_url or None
        )
        if settings.telegram_bot_token
        else None
    )
    app.state.telegram_channel = telegram_channel

    vk_channel = (
        VkChannel(token=settings.vk_group_token, group_id=settings.vk_group_id)
        if settings.vk_group_token
        else None
    )
    app.state.vk_channel = vk_channel

    task = asyncio.create_task(
        background.run_background_loop(db, settings, telegram_channel, vk_channel)
    )
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    if telegram_channel is not None:
        await telegram_channel.bot.session.close()
    if vk_channel is not None:
        await vk_channel.bot.api.http_client.close()
    db.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Raffle Platform API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # JWT только через заголовок, без cookie (п.11.1 ТЗ)
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        path = request.url.path
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(duration)
        return response

    @app.get("/health", tags=["infra"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", tags=["infra"])
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    api_prefix = "/api"
    app.include_router(auth.router, prefix=api_prefix)
    app.include_router(dashboard.router, prefix=api_prefix)
    app.include_router(participants.router, prefix=api_prefix)
    app.include_router(giveaways.router, prefix=api_prefix)
    app.include_router(manual_registrations.router, prefix=api_prefix)
    app.include_router(sales.router, prefix=api_prefix)
    app.include_router(bank_reconciliation.router, prefix=api_prefix)
    app.include_router(tickets.router, prefix=api_prefix)
    app.include_router(panel_users.router, prefix=api_prefix)
    app.include_router(settings_api.router, prefix=api_prefix)
    app.include_router(audit.router, prefix=api_prefix)
    app.include_router(broadcasts.router, prefix=api_prefix)
    app.include_router(reports.router, prefix=api_prefix)

    return app


app = create_app()
