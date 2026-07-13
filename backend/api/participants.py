"""Раздел «Участники» (п.11.4, 11.3, 14.2 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.enums import AuditActorType
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.services import audit_service
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import ParticipantOut, ParticipantUpdateRequest

router = APIRouter(prefix="/participants", tags=["participants"])


@router.get("", response_model=list[ParticipantOut])
def list_participants(
    q: str | None = None,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> list[Participant]:
    stmt = select(Participant).order_by(Participant.id.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Participant.phone.like(like), Participant.full_name.like(like)))
    return list(session.execute(stmt).scalars())


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
