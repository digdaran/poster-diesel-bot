"""Раздел «Рассылки» — только Telegram (п.15 ТЗ)."""

from __future__ import annotations

import datetime as dt

from app.core.db import Database
from app.core.permissions import Permission
from app.models.broadcast import Broadcast
from app.models.enums import AuditActorType
from app.models.panel_user import PanelUser
from app.services import audit_service
from app.services import broadcast_service as svc
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_database, get_session, require_permission

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])


class BroadcastOut(BaseModel):
    id: int
    title: str
    message_text: str
    audience_filter: dict
    status: str
    stats: dict
    created_at: dt.datetime
    sent_at: dt.datetime | None

    model_config = {"from_attributes": True}


class BroadcastCreateRequest(BaseModel):
    title: str
    message_text: str
    audience_filter: dict = {}


@router.get("", response_model=list[BroadcastOut])
def list_broadcasts(
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.BROADCAST_VIEW)),
) -> list[Broadcast]:
    return list(session.execute(select(Broadcast).order_by(Broadcast.id.desc())).scalars())


@router.post("", response_model=BroadcastOut, status_code=status.HTTP_201_CREATED)
def create_broadcast(
    payload: BroadcastCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.BROADCAST_SEND)),
) -> Broadcast:
    broadcast = svc.create_broadcast(
        session,
        title=payload.title,
        message_text=payload.message_text,
        audience_filter=payload.audience_filter,
    )
    audit_service.log(
        session,
        action="broadcast_create",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="broadcast",
        entity_id=broadcast.id,
        ip_address=request.client.host if request.client else None,
    )
    return broadcast


@router.post("/{broadcast_id}/send", response_model=BroadcastOut)
def send_broadcast(
    broadcast_id: int,
    request: Request,
    db: Database = Depends(get_database),
    user: PanelUser = Depends(require_permission(Permission.BROADCAST_SEND)),
) -> Broadcast:
    """Реальная отправка через Telegram подключается каналом (M9) — здесь
    используется заглушка `send_fn`, которая всегда возвращает True, пока канал
    Telegram не зарегистрирован (см. app.channels.factory, TODO в M9)."""
    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рассылка не найдена")

    svc.send_broadcast(
        db, broadcast_id=broadcast_id, send_fn=lambda ext, text: True, rate_limit_delay_sec=0.0
    )

    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        audit_service.log(
            session,
            action="broadcast_send",
            actor_type=AuditActorType.PANEL_USER,
            actor_id=user.id,
            actor_label=user.login,
            entity_type="broadcast",
            entity_id=broadcast_id,
            details=broadcast.stats,
            ip_address=request.client.host if request.client else None,
        )
        session.expunge(broadcast)
    return broadcast
