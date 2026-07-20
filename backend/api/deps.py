"""FastAPI-зависимости: доступ к БД, текущий пользователь панели, проверка прав
(п.11.2 ТЗ — единственное место, где эндпоинты проверяют права на сервере)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.permissions import Permission, permissions_for_role, role_has_permission
from app.core.security import TokenError, decode_token
from app.models.panel_user import PanelUser
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from channels.telegram.channel import TelegramChannel

_bearer = HTTPBearer(auto_error=False)


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_telegram_channel(request: Request) -> TelegramChannel | None:
    """Outbound-only инстанс для проактивных уведомлений (см. backend/main.py
    lifespan) — `None`, если `TELEGRAM_BOT_TOKEN` не задан (dev/тесты)."""
    return request.app.state.telegram_channel


def get_session(request: Request) -> Iterator[Session]:
    db: Database = request.app.state.db
    with db.session() as session:
        yield session


def get_settings_dep() -> Settings:
    return get_settings()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> PanelUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")
    try:
        decoded = decode_token(
            credentials.credentials, secret=settings.jwt_secret, expected_type="access"
        )
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = session.get(PanelUser, decoded.user_id)
    if user is None or user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден или заблокирован",
        )
    return user


def require_permission(permission: Permission) -> Callable[..., PanelUser]:
    """Возвращает FastAPI-зависимость, проверяющую наличие разрешения у роли
    текущего пользователя. Недоступные эндпоинты возвращают 403 (п.11.2, 11.3 ТЗ)."""

    def _dependency(user: PanelUser = Depends(get_current_user)) -> PanelUser:
        from app.core.permissions import PanelRole

        role = PanelRole(user.role.value)
        if not role_has_permission(role, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        return user

    return _dependency


def user_permissions(user: PanelUser) -> list[str]:
    from app.core.permissions import PanelRole

    role = PanelRole(user.role.value)
    return sorted(p.value for p in permissions_for_role(role))
