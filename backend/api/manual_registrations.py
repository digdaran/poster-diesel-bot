"""Раздел «Ручные регистрации» (п.7.7, 8.2, 11.4, 14.2, 14.3 ТЗ)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.db import Database
from app.core.permissions import PanelRole, Permission
from app.models.enums import AuditActorType
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.services import audit_service, participant_service
from app.services import manual_registration_service as svc
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_database, get_session, get_settings_dep, require_permission
from backend.api.schemas import ManualRegistrationCreateRequest, ManualRegistrationOut

router = APIRouter(prefix="/manual-registrations", tags=["manual-registrations"])


@router.get("", response_model=list[ManualRegistrationOut])
def list_manual_registrations(
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> list[ManualRegistration]:
    """Operator видит только СВОИ регистрации (п.14.2 ТЗ), Super Admin/Administrator — все."""
    stmt = select(ManualRegistration).order_by(ManualRegistration.id.desc())
    if PanelRole(user.role.value) == PanelRole.OPERATOR:
        stmt = stmt.where(ManualRegistration.operator_id == user.id)
    return list(session.execute(stmt).scalars())


@router.post("", response_model=ManualRegistrationOut, status_code=status.HTTP_201_CREATED)
def create_manual_registration(
    payload: ManualRegistrationCreateRequest,
    request: Request,
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings_dep),
    user: PanelUser = Depends(require_permission(Permission.MANUAL_REGISTRATION_CREATE)),
) -> ManualRegistration:
    with db.session() as session:
        participant = participant_service.resolve_manual_recipient(
            session, payload.participant_phone
        )
        participant_id = participant.id

    outcome = svc.create_manual_registration_safe(
        db,
        giveaway_id=payload.giveaway_id,
        participant_id=participant_id,
        quantity=payload.quantity,
        operator_id=user.id,
        ttl_seconds=settings.manual_reservation_ttl_sec,
        comment=payload.comment,
    )
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Недостаточно свободных номеров: доступно {outcome.free_count}",
        )

    with db.session() as session:
        registration = session.get(ManualRegistration, outcome.manual_registration_id)
        assert registration is not None  # только что создана в этой же транзакции
        audit_service.log(
            session,
            action="manual_registration_create",
            actor_type=AuditActorType.PANEL_USER,
            actor_id=user.id,
            actor_label=user.login,
            entity_type="manual_registration",
            entity_id=outcome.manual_registration_id,
            ip_address=request.client.host if request.client else None,
        )
        session.expunge(registration)
    return registration


@router.post("/{registration_id}/confirm", response_model=ManualRegistrationOut)
def confirm_manual_registration(
    registration_id: int,
    request: Request,
    db: Database = Depends(get_database),
    user: PanelUser = Depends(require_permission(Permission.MANUAL_REGISTRATION_CONFIRM)),
) -> ManualRegistration:
    try:
        svc.confirm_manual_registration(db, manual_registration_id=registration_id)
    except svc.ManualRegistrationStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    with db.session() as session:
        registration = session.get(ManualRegistration, registration_id)
        assert registration is not None
        audit_service.log(
            session,
            action="manual_registration_confirm",
            actor_type=AuditActorType.PANEL_USER,
            actor_id=user.id,
            actor_label=user.login,
            entity_type="manual_registration",
            entity_id=registration_id,
            ip_address=request.client.host if request.client else None,
        )
        session.expunge(registration)
    return registration


@router.post("/{registration_id}/cancel", response_model=ManualRegistrationOut)
def cancel_manual_registration(
    registration_id: int,
    request: Request,
    db: Database = Depends(get_database),
    user: PanelUser = Depends(require_permission(Permission.MANUAL_REGISTRATION_CANCEL)),
) -> ManualRegistration:
    try:
        svc.cancel_manual_registration(db, manual_registration_id=registration_id)
    except svc.ManualRegistrationStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    with db.session() as session:
        registration = session.get(ManualRegistration, registration_id)
        assert registration is not None
        audit_service.log(
            session,
            action="manual_registration_cancel",
            actor_type=AuditActorType.PANEL_USER,
            actor_id=user.id,
            actor_label=user.login,
            entity_type="manual_registration",
            entity_id=registration_id,
            ip_address=request.client.host if request.client else None,
        )
        session.expunge(registration)
    return registration
