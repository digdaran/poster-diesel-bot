"""Служебное состояние платформы для панели: доступность Telegram для плашки
«Telegram недоступен» (DECISIONS_LOG.md №94)."""

from __future__ import annotations

from app.models.enums import ChannelType, PendingDeliveryStatus
from app.models.panel_user import PanelUser
from app.models.pending_channel_delivery import PendingChannelDelivery
from app.services.channel_health_service import ChannelHealth
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_session
from backend.api.schemas import ChannelHealthOut, SystemChannelHealthOut

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/channel-health", response_model=SystemChannelHealthOut)
def channel_health(
    request: Request,
    session: Session = Depends(get_session),
    # Без отдельного права: о сбое должен узнать любой, кто сидит в панели.
    _user: PanelUser = Depends(get_current_user),
) -> SystemChannelHealthOut:
    health: ChannelHealth | None = getattr(request.app.state, "telegram_health", None)
    pending = session.scalar(
        select(func.count())
        .select_from(PendingChannelDelivery)
        .where(
            PendingChannelDelivery.channel == ChannelType.TELEGRAM,
            PendingChannelDelivery.status == PendingDeliveryStatus.PENDING,
        )
    )
    return SystemChannelHealthOut(
        telegram=(
            ChannelHealthOut(
                ok=health.ok,
                failing_since=health.failing_since,
                last_checked_at=health.last_checked_at,
            )
            if health is not None
            else None
        ),
        pending_telegram_deliveries=pending or 0,
    )
