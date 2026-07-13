"""Сервис пользователей веб-панели: аутентификация, JWT access+refresh, бутстрап
первого Super Admin (п.11.1, 12 ТЗ)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import PanelRole
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.base import utcnow
from app.models.enums import PanelUserRole
from app.models.panel_user import PanelUser


class AuthError(Exception):
    """Неверный логин/пароль, заблокированный пользователь, невалидный токен."""


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


def bootstrap_superadmin(session: Session, *, login: str, password: str) -> PanelUser | None:
    """Создаёт первого Super Admin, если таблица `panel_users` пуста (п.12 ТЗ).

    No-op (возвращает None), если пользователи уже есть — безопасно вызывать
    при каждом старте backend.
    """
    existing = session.execute(select(PanelUser).limit(1)).scalar_one_or_none()
    if existing is not None:
        return None
    if not password:
        raise ValueError("SUPERADMIN_PASSWORD не задан — бутстрап невозможен")
    user = PanelUser(
        login=login, password_hash=hash_password(password), role=PanelUserRole.SUPER_ADMIN
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, *, login: str, password: str) -> PanelUser:
    user = session.execute(select(PanelUser).where(PanelUser.login == login)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Неверный логин или пароль")
    if user.is_blocked:
        raise AuthError("Учётная запись заблокирована")
    user.last_login_at = utcnow()
    session.flush()
    return user


def issue_tokens(
    user: PanelUser, *, secret: str, access_ttl_min: int, refresh_ttl_days: int
) -> TokenPair:
    role = PanelRole(user.role.value)
    return TokenPair(
        access_token=create_access_token(
            user_id=user.id, role=role, secret=secret, ttl_minutes=access_ttl_min
        ),
        refresh_token=create_refresh_token(
            user_id=user.id, role=role, secret=secret, ttl_days=refresh_ttl_days
        ),
    )


def refresh_access_token(
    session: Session, *, refresh_token: str, secret: str, access_ttl_min: int
) -> str:
    """Выпускает новый access-токен по refresh-токену (п.11.1 ТЗ).

    Проверяет, что пользователь всё ещё существует и не заблокирован (п.18.2 ТЗ) —
    единственный механизм отзыва в первой версии (см. DECISIONS.md, п.7).
    """
    decoded = decode_token(refresh_token, secret=secret, expected_type="refresh")
    user = session.get(PanelUser, decoded.user_id)
    if user is None or user.is_blocked:
        raise AuthError("Пользователь не найден или заблокирован")
    return create_access_token(
        user_id=user.id, role=PanelRole(user.role.value), secret=secret, ttl_minutes=access_ttl_min
    )
