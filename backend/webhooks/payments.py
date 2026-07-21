"""Webhook-роутеры банков (п.9.4, 9.5, 17.1 ТЗ).

Единственные эндпоинты, публикуемые наружу (см. ARCHITECTURE.md, Caddy). Каждый
роутер верифицирует подпись СВОИМ провайдером (по URL), независимо от того, какой
провайдер сейчас активен для НОВЫХ платежей — уже созданный платёж дообрабатывается
тем банком, через который был создан (п.9.3 ТЗ).
"""

from __future__ import annotations

import structlog
from app.core.config import Settings
from app.core.db import Database
from app.models.enums import AuditActorType
from app.payments.base import BasePaymentProvider, WebhookVerificationError
from app.payments.tbank import TBankProvider
from app.payments.vtb import VTBProvider
from app.services import audit_service, notification_service
from app.services import payment_service as svc
from fastapi import APIRouter, Depends, Request, Response, status

from backend.api.deps import get_database, get_settings_dep, get_telegram_channel, get_vk_channel
from channels.telegram.channel import TelegramChannel
from channels.vk.channel import VkChannel

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)


async def _handle(
    provider: BasePaymentProvider,
    request: Request,
    body: bytes,
    db: Database,
    provider_name: str,
    telegram_channel: TelegramChannel | None,
    vk_channel: VkChannel | None,
) -> Response:
    ip = request.client.host if request.client else None
    try:
        event = provider.verify_and_parse_webhook(headers=dict(request.headers), body=body)
    except WebhookVerificationError as exc:
        with db.session() as session:
            audit_service.log(
                session,
                action="webhook_rejected",
                actor_type=AuditActorType.BANK,
                actor_label=provider_name,
                details={"reason": str(exc)},
                ip_address=ip,
            )
        logger.warning("webhook_rejected", provider=provider_name, reason=str(exc))
        return Response(status_code=status.HTTP_400_BAD_REQUEST, content="rejected")

    outcome = svc.finalize_payment(
        db, order_id=event.order_id, new_status=event.status, raw_payload=event.raw_payload
    )
    with db.session() as session:
        audit_service.log(
            session,
            action="payment_webhook_finalize" if outcome.applied else "payment_webhook_duplicate",
            actor_type=AuditActorType.BANK,
            actor_label=provider_name,
            entity_type="payment",
            entity_id=outcome.payment_id,
            details={
                "order_id": event.order_id,
                "status": event.status.value,
                "applied": outcome.applied,
            },
            ip_address=ip,
        )
    if outcome.applied:
        await notification_service.notify_payment_outcome(
            db, outcome, telegram_channel=telegram_channel, vk_channel=vk_channel
        )
    elif outcome.late_success_no_tickets:
        await notification_service.notify_late_success_no_tickets(
            db, outcome, telegram_channel=telegram_channel, vk_channel=vk_channel
        )
    return Response(status_code=status.HTTP_200_OK, content="ok")


@router.post("/tbank")
async def tbank_webhook(
    request: Request,
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings_dep),
    telegram_channel: TelegramChannel | None = Depends(get_telegram_channel),
    vk_channel: VkChannel | None = Depends(get_vk_channel),
) -> Response:
    body = await request.body()
    provider = TBankProvider.from_settings(settings)
    return await _handle(provider, request, body, db, "tbank", telegram_channel, vk_channel)


@router.post("/vtb")
async def vtb_webhook(
    request: Request,
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings_dep),
    telegram_channel: TelegramChannel | None = Depends(get_telegram_channel),
    vk_channel: VkChannel | None = Depends(get_vk_channel),
) -> Response:
    body = await request.body()
    provider = VTBProvider.from_settings(settings)
    return await _handle(provider, request, body, db, "vtb", telegram_channel, vk_channel)
