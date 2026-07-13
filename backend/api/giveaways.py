"""Раздел «Розыгрыши» (п.7.2, 11.4, 14.2 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.enums import AuditActorType
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.services import audit_service
from app.services import ticket_pool_service as pool_svc
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import GiveawayCreateRequest, GiveawayOut, GiveawayUpdateRequest

router = APIRouter(prefix="/giveaways", tags=["giveaways"])


@router.get("", response_model=list[GiveawayOut])
def list_giveaways(
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_GIVEAWAYS)),
) -> list[Giveaway]:
    return list(session.execute(select(Giveaway).order_by(Giveaway.id.desc())).scalars())


@router.get("/{giveaway_id}", response_model=GiveawayOut)
def get_giveaway(
    giveaway_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_GIVEAWAYS)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    return giveaway


@router.post("", response_model=GiveawayOut, status_code=status.HTTP_201_CREATED)
def create_giveaway(
    payload: GiveawayCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    giveaway = Giveaway(
        name=payload.name,
        prefix=payload.prefix,
        ticket_price=payload.ticket_price,
        max_tickets=payload.max_tickets,
        digital_poster_caption=payload.digital_poster_caption,
    )
    session.add(giveaway)
    session.flush()
    audit_service.log(
        session,
        action="giveaway_create",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.patch("/{giveaway_id}", response_model=GiveawayOut)
def update_giveaway(
    giveaway_id: int,
    payload: GiveawayUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    """prefix/ticket_price/max_tickets неизменяемы после opened_at (п.7.2 ТЗ) —
    их нет в GiveawayUpdateRequest, поэтому такие правки в принципе невозможны."""
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    if payload.name is not None:
        giveaway.name = payload.name
    if payload.digital_poster_caption is not None:
        giveaway.digital_poster_caption = payload.digital_poster_caption
    session.flush()
    audit_service.log(
        session,
        action="giveaway_update",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/open", response_model=GiveawayOut)
def open_registration(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    try:
        pool_svc.open_registration(session, giveaway)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.log(
        session,
        action="giveaway_open_registration",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/lock", response_model=GiveawayOut)
def lock_giveaway(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_LOCK)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_locked = True
    session.flush()
    audit_service.log(
        session,
        action="giveaway_lock",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/unlock", response_model=GiveawayOut)
def unlock_giveaway(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_LOCK)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_locked = False
    session.flush()
    audit_service.log(
        session,
        action="giveaway_unlock",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/close-registration", response_model=GiveawayOut)
def close_registration(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_registration_open = False
    session.flush()
    audit_service.log(
        session,
        action="giveaway_close_registration",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway
