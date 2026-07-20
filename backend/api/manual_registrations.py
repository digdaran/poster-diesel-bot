"""Раздел «Ручные регистрации» (п.7.7, 8.2, 11.4, 14.2, 14.3 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.config import Settings
from app.core.db import Database
from app.core.permissions import PanelRole, Permission
from app.models.enums import AuditActorType, ManualRegistrationStatus
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.services import audit_service, participant_service
from app.services import manual_registration_service as svc
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from backend.api.deps import get_database, get_session, get_settings_dep, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import ManualRegistrationCreateRequest, ManualRegistrationOut

router = APIRouter(prefix="/manual-registrations", tags=["manual-registrations"])


@router.get("", response_model=None)
def list_manual_registrations(
    giveaway_id: int | None = None,
    participant_query: str | None = None,
    status_filter: ManualRegistrationStatus | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    page: int = 1,
    page_size: int = 50,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> dict[str, Any] | list[dict[str, Any]] | Response:
    """Operator видит только СВОИ регистрации (п.14.2 ТЗ), Super Admin/Administrator — все."""
    validate_page_size(page_size)
    stmt = select(ManualRegistration)
    if PanelRole(user.role.value) == PanelRole.OPERATOR:
        stmt = stmt.where(ManualRegistration.operator_id == user.id)
    if giveaway_id is not None:
        stmt = stmt.where(ManualRegistration.giveaway_id == giveaway_id)
    if status_filter is not None:
        stmt = stmt.where(ManualRegistration.status == status_filter)
    if created_from is not None:
        stmt = stmt.where(ManualRegistration.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(ManualRegistration.created_at < created_to + dt.timedelta(days=1))
    if participant_query:
        like = f"%{participant_query}%"
        stmt = stmt.join(ManualRegistration.participant).where(
            or_(Participant.phone.like(like), Participant.full_name.like(like))
        )
    participant_load = (
        contains_eager(ManualRegistration.participant)
        if participant_query
        else joinedload(ManualRegistration.participant)
    )
    eager_options = (
        participant_load,
        joinedload(ManualRegistration.giveaway),
        joinedload(ManualRegistration.operator),
    )

    if export is not None:
        eager_stmt = stmt.options(*eager_options).order_by(ManualRegistration.id.desc())
        rows = [
            _to_out(session, r).model_dump(mode="json")
            for r in session.execute(eager_stmt).scalars()
        ]
        return maybe_export(
            rows, export, user, "manual_registrations", permission=Permission.SALES_EXPORT
        )

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    eager_stmt = (
        stmt.options(*eager_options)
        .order_by(ManualRegistration.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = session.execute(eager_stmt).scalars()
    return {
        "items": [_to_out(session, r).model_dump(mode="json") for r in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _to_out(session: Session, registration: ManualRegistration) -> ManualRegistrationOut:
    return ManualRegistrationOut(
        id=registration.id,
        participant_id=registration.participant_id,
        participant_phone=registration.participant.phone,
        participant_full_name=registration.participant.full_name,
        giveaway_id=registration.giveaway_id,
        giveaway_name=registration.giveaway.name,
        quantity=registration.quantity,
        revenue=registration.quantity * registration.giveaway.ticket_price,
        status=registration.status.value,
        operator_id=registration.operator_id,
        operator_login=registration.operator.login,
        comment=registration.comment,
        created_at=registration.created_at,
        confirmed_at=registration.confirmed_at,
        cancelled_at=registration.cancelled_at,
    )


@router.post("", response_model=ManualRegistrationOut, status_code=status.HTTP_201_CREATED)
def create_manual_registration(
    payload: ManualRegistrationCreateRequest,
    request: Request,
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings_dep),
    user: PanelUser = Depends(require_permission(Permission.MANUAL_REGISTRATION_CREATE)),
) -> ManualRegistrationOut:
    # Имя уже существующего участника может изменить только Super Admin
    # (Operator/Administrator видят его в форме как нередактируемое) — см.
    # DECISIONS.md. Enforced здесь, а не только в UI (п. "Permissions всегда на
    # сервере" из CLAUDE.md).
    is_super_admin = PanelRole(user.role.value) == PanelRole.SUPER_ADMIN
    with db.session() as session:
        participant = participant_service.resolve_manual_recipient(
            session,
            payload.participant_phone,
            full_name=payload.participant_full_name,
            allow_overwrite=is_super_admin,
        )
        participant_id = participant.id

    try:
        outcome = svc.create_manual_registration_safe(
            db,
            giveaway_id=payload.giveaway_id,
            participant_id=participant_id,
            quantity=payload.quantity,
            operator_id=user.id,
            ttl_seconds=settings.manual_reservation_ttl_sec,
            comment=payload.comment,
        )
    except svc.GiveawayNotSellableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not outcome.ok:
        if outcome.has_active_purchase:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="У участника уже есть активная покупка (ожидает оплаты/подтверждения) — "
                "новая регистрация невозможна, пока та не завершится",
            )
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
        return _to_out(session, registration)


@router.post("/{registration_id}/confirm", response_model=ManualRegistrationOut)
def confirm_manual_registration(
    registration_id: int,
    request: Request,
    db: Database = Depends(get_database),
    user: PanelUser = Depends(require_permission(Permission.MANUAL_REGISTRATION_CONFIRM)),
) -> ManualRegistrationOut:
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
        return _to_out(session, registration)


@router.post("/{registration_id}/cancel", response_model=ManualRegistrationOut)
def cancel_manual_registration(
    registration_id: int,
    request: Request,
    db: Database = Depends(get_database),
    user: PanelUser = Depends(require_permission(Permission.MANUAL_REGISTRATION_CANCEL)),
) -> ManualRegistrationOut:
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
        return _to_out(session, registration)
