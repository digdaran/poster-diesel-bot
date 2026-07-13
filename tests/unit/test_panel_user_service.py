"""Тесты аутентификации панели и бутстрапа Super Admin (п.11.1, 12, 18.2 ТЗ)."""

from __future__ import annotations

import pytest
from app.services import panel_user_service as svc
from sqlalchemy.orm import Session


def test_bootstrap_creates_first_superadmin(session: Session) -> None:
    user = svc.bootstrap_superadmin(session, login="admin", password="strongpass123")
    assert user is not None
    assert user.role.value == "super_admin"


def test_bootstrap_is_noop_when_users_exist(session: Session) -> None:
    svc.bootstrap_superadmin(session, login="admin", password="strongpass123")
    second = svc.bootstrap_superadmin(session, login="admin2", password="another")
    assert second is None


def test_authenticate_success_and_updates_last_login(session: Session) -> None:
    svc.bootstrap_superadmin(session, login="admin", password="strongpass123")
    user = svc.authenticate(session, login="admin", password="strongpass123")
    assert user.last_login_at is not None


def test_authenticate_wrong_password_raises(session: Session) -> None:
    svc.bootstrap_superadmin(session, login="admin", password="strongpass123")
    with pytest.raises(svc.AuthError):
        svc.authenticate(session, login="admin", password="wrong")


def test_authenticate_blocked_user_raises(session: Session) -> None:
    user = svc.bootstrap_superadmin(session, login="admin", password="strongpass123")
    user.is_blocked = True
    session.flush()
    with pytest.raises(svc.AuthError):
        svc.authenticate(session, login="admin", password="strongpass123")


def test_issue_and_refresh_tokens(session: Session) -> None:
    user = svc.bootstrap_superadmin(session, login="admin", password="strongpass123")
    tokens = svc.issue_tokens(
        user, secret="s3cr3t-test-key", access_ttl_min=15, refresh_ttl_days=30
    )
    assert tokens.access_token != tokens.refresh_token
    new_access = svc.refresh_access_token(
        session, refresh_token=tokens.refresh_token, secret="s3cr3t-test-key", access_ttl_min=15
    )
    assert new_access
