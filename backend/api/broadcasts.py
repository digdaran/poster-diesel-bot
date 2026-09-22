"""Раздел «Рассылки» — только Telegram (п.15 ТЗ).

Реальная отправка идёт в фоне (`BackgroundTasks`) — при большой аудитории
рассылка может занимать секунды-минуты, HTTP-ответ не должен блокироваться на
всё это время. Эндпоинт синхронно переводит DRAFT -> SENDING
(`broadcast_service.mark_broadcast_sending`) и сразу возвращает управление;
реальную доставку и финальный переход в SENT/FAILED делает
`broadcast_service.send_broadcast` в фоне, используя ту же инфраструктуру
гарантированной доставки, что и проактивные уведомления
(`app/services/channel_delivery_queue.py`, DECISIONS_LOG.md №79)."""

from __future__ import annotations

import datetime as dt

from app.core.db import Database
from app.core.permissions import Permission
from app.models.broadcast import Broadcast
from app.models.enums import AuditActorType
from app.models.panel_user import PanelUser
from app.services import audit_service
from app.services import broadcast_service as svc
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_database, get_session, get_telegram_channel, require_permission
from channels.telegram.channel import TelegramChannel

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


async def _send_broadcast_background(
    *,
    db: Database,
    broadcast_id: int,
    telegram_channel: TelegramChannel | None,
    actor_id: int,
    actor_login: str,
    ip_address: str | None,
) -> None:
    result = await svc.send_broadcast(
        db, broadcast_id=broadcast_id, telegram_channel=telegram_channel
    )
    with db.session() as session:
        audit_service.log(
            session,
            action="broadcast_send",
            actor_type=AuditActorType.PANEL_USER,
            actor_id=actor_id,
            actor_label=actor_login,
            entity_type="broadcast",
            entity_id=broadcast_id,
            details={
                "recipients": result.recipients,
                "delivered": result.delivered,
                "queued": result.queued,
                "errors": result.errors,
            },
            ip_address=ip_address,
        )


@router.post("/{broadcast_id}/send", response_model=BroadcastOut)
def send_broadcast(
    broadcast_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_database),
    user: PanelUser = Depends(require_permission(Permission.BROADCAST_SEND)),
    telegram_channel: TelegramChannel | None = Depends(get_telegram_channel),
) -> Broadcast:
    try:
        broadcast = svc.mark_broadcast_sending(db, broadcast_id=broadcast_id)
    except svc.BroadcastNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Рассылка не найдена"
        ) from exc
    except svc.BroadcastNotDraftError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    background_tasks.add_task(
        _send_broadcast_background,
        db=db,
        broadcast_id=broadcast_id,
        telegram_channel=telegram_channel,
        actor_id=user.id,
        actor_login=user.login,
        ip_address=request.client.host if request.client else None,
    )
    return broadcast
