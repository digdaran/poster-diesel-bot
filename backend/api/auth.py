"""Аутентификация панели: логин/пароль -> JWT access+refresh, без cookie (п.11.1 ТЗ)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.permissions import PanelRole
from app.models.enums import AuditActorType
from app.models.panel_user import PanelUser
from app.services import audit_service
from app.services import panel_user_service as svc
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_session, get_settings_dep, user_permissions
from backend.api.schemas import (
    AccessTokenResponse,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    TokenPairResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPairResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TokenPairResponse:
    ip = request.client.host if request.client else None
    try:
        user = svc.authenticate(session, login=payload.login, password=payload.password)
    except svc.AuthError as exc:
        audit_service.log(
            session,
            action="panel_login_failed",
            actor_type=AuditActorType.PANEL_USER,
            actor_label=payload.login,
            ip_address=ip,
            details={"reason": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    tokens = svc.issue_tokens(
        user,
        secret=settings.jwt_secret,
        access_ttl_min=settings.jwt_access_ttl_min,
        refresh_ttl_days=settings.jwt_refresh_ttl_days,
    )
    audit_service.log(
        session,
        action="panel_login_success",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        ip_address=ip,
    )
    return TokenPairResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(
    payload: RefreshRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> AccessTokenResponse:
    try:
        access_token = svc.refresh_access_token(
            session,
            refresh_token=payload.refresh_token,
            secret=settings.jwt_secret,
            access_ttl_min=settings.jwt_access_ttl_min,
        )
    except svc.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AccessTokenResponse(access_token=access_token)


@router.get("/me", response_model=MeResponse)
def me(user: PanelUser = Depends(get_current_user)) -> MeResponse:
    role = PanelRole(user.role.value)
    return MeResponse(
        id=user.id, login=user.login, role=role.value, permissions=user_permissions(user)
    )
