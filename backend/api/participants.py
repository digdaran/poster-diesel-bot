"""Раздел «Участники» (п.11.4, 11.3, 14.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.permissions import Permission
from app.core.phone import InvalidPhoneError
from app.models.enums import AuditActorType
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.services import audit_service, participant_service
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.api.deps import get_session, require_permission
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import ParticipantOut, ParticipantUpdateRequest

router = APIRouter(prefix="/participants", tags=["participants"])


@router.get("", response_model=None)
def list_participants(
    q: str | None = None,
    phone_verified: bool | None = None,
    is_blocked: bool | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    page: int = 1,
    page_size: int = 50,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> dict[str, Any]:
    validate_page_size(page_size)
    stmt = select(Participant)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Participant.phone.like(like), Participant.full_name.like(like)))
    if phone_verified is not None:
        stmt = stmt.where(Participant.phone_verified == phone_verified)
    if is_blocked is not None:
        stmt = stmt.where(Participant.is_blocked == is_blocked)
    if created_from is not None:
        stmt = stmt.where(Participant.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Participant.created_at < created_to + dt.timedelta(days=1))

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    page_stmt = (
        stmt.options(selectinload(Participant.channel_bindings))
        .order_by(Participant.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = session.execute(page_stmt).scalars().all()
    return {
        "items": [ParticipantOut.model_validate(p).model_dump(mode="json") for p in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/by-phone", response_model=ParticipantOut | None)
def find_participant_by_phone(
    phone: str,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> Participant | None:
    """Поиск участника по номеру для автоподстановки имени в форме ручной
    регистрации (п.14.2/14.3 ТЗ — см. DECISIONS.md). Должен быть объявлен ДО
    `/{participant_id}`, иначе FastAPI попытается разобрать "by-phone" как id."""
    try:
        return participant_service.find_by_phone(session, phone)
    except InvalidPhoneError:
        return None


@router.get("/{participant_id}", response_model=ParticipantOut)
def get_participant(
    participant_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    return participant


@router.patch("/{participant_id}", response_model=ParticipantOut)
def update_participant(
    participant_id: int,
    payload: ParticipantUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PARTICIPANT_EDIT)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    if payload.full_name is not None:
        participant.full_name = payload.full_name
    session.flush()
    audit_service.log(
        session,
        action="participant_edit",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="participant",
        entity_id=participant.id,
        ip_address=request.client.host if request.client else None,
    )
    return participant


@router.post("/{participant_id}/block", response_model=ParticipantOut)
def block_participant(
    participant_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PARTICIPANT_BLOCK)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    participant.is_blocked = True
    session.flush()
    audit_service.log(
        session,
        action="participant_block",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="participant",
        entity_id=participant.id,
        ip_address=request.client.host if request.client else None,
    )
    return participant


@router.post("/{participant_id}/unblock", response_model=ParticipantOut)
def unblock_participant(
    participant_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PARTICIPANT_BLOCK)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    participant.is_blocked = False
    session.flush()
    audit_service.log(
        session,
        action="participant_unblock",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="participant",
        entity_id=participant.id,
        ip_address=request.client.host if request.client else None,
    )
    return participant
