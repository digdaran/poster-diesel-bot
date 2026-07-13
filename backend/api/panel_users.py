"""Раздел «Пользователи панели» — только Super Admin (п.11.4, 12 ТЗ)."""

from __future__ import annotations

from app.core.permissions import PanelRole, Permission
from app.core.security import hash_password
from app.models.enums import AuditActorType, PanelUserRole
from app.models.panel_user import PanelUser
from app.services import audit_service
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import PanelUserCreateRequest, PanelUserOut, PanelUserUpdateRequest

router = APIRouter(prefix="/panel-users", tags=["panel-users"])


@router.get("", response_model=list[PanelUserOut])
def list_panel_users(
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.PANEL_USERS_MANAGE)),
) -> list[PanelUser]:
    return list(session.execute(select(PanelUser).order_by(PanelUser.id)).scalars())


@router.post("", response_model=PanelUserOut, status_code=status.HTTP_201_CREATED)
def create_panel_user(
    payload: PanelUserCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PANEL_USERS_MANAGE)),
) -> PanelUser:
    try:
        role = PanelRole(payload.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Неизвестная роль: {payload.role}"
        ) from exc

    new_user = PanelUser(
        login=payload.login,
        password_hash=hash_password(payload.password),
        role=PanelUserRole(role.value),
    )
    session.add(new_user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Логин уже занят") from exc

    audit_service.log(
        session,
        action="panel_user_create",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="panel_user",
        entity_id=new_user.id,
        details={"role": role.value},
        ip_address=request.client.host if request.client else None,
    )
    return new_user


@router.patch("/{panel_user_id}", response_model=PanelUserOut)
def update_panel_user(
    panel_user_id: int,
    payload: PanelUserUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PANEL_USERS_MANAGE)),
) -> PanelUser:
    target = session.get(PanelUser, panel_user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    details: dict[str, object] = {}
    if payload.is_blocked is not None:
        target.is_blocked = payload.is_blocked
        details["is_blocked"] = payload.is_blocked
    if payload.role is not None:
        role = PanelRole(payload.role)
        target.role = PanelUserRole(role.value)
        details["role"] = role.value
    if payload.password is not None:
        target.password_hash = hash_password(payload.password)
        details["password_changed"] = True
    session.flush()

    audit_service.log(
        session,
        action="panel_user_update",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="panel_user",
        entity_id=target.id,
        details=details,
        ip_address=request.client.host if request.client else None,
    )
    return target
