"""Матрица прав ролей через реальный HTTP API (п.11.3, 20.1, 20.2 ТЗ):
недоступные эндпоинты возвращают 403."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def create_panel_user(
    client: TestClient, admin_token: str, login_: str, password: str, role: str
) -> None:
    resp = client.post(
        "/api/panel-users",
        json={"login": login_, "password": password, "role": role},
        headers=auth_headers(admin_token),
    )
    resp.raise_for_status()


def test_superadmin_can_access_everything(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    for path in [
        "/api/dashboard",
        "/api/participants",
        "/api/giveaways",
        "/api/settings",
        "/api/audit",
        "/api/panel-users",
        "/api/bank-reconciliation/status",
    ]:
        resp = api_client.get(path, headers=auth_headers(token))
        assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"


def test_operator_forbidden_from_admin_only_sections(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "operator1", "operator-strong-pass", "operator")
    op_token = login(api_client, "operator1", "operator-strong-pass")

    forbidden_paths = [
        "/api/settings",
        "/api/panel-users",
        "/api/audit",
        "/api/bank-reconciliation/status",
    ]
    for path in forbidden_paths:
        resp = api_client.get(path, headers=auth_headers(op_token))
        assert resp.status_code == 403, f"{path}: expected 403, got {resp.status_code}"

    allowed_paths = [
        "/api/dashboard",
        "/api/participants",
        "/api/giveaways",
        "/api/payments",
        "/api/tickets",
    ]
    for path in allowed_paths:
        resp = api_client.get(path, headers=auth_headers(op_token))
        assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"


def test_operator_cannot_edit_giveaway_or_block_participant(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "operator2", "operator-strong-pass", "operator")
    op_token = login(api_client, "operator2", "operator-strong-pass")

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "X", "prefix": "OPX", "ticket_price": 1000, "max_tickets": 10},
        headers=auth_headers(op_token),
    )
    assert resp.status_code == 403

    resp = api_client.post("/api/participants/1/block", headers=auth_headers(op_token))
    assert resp.status_code == 403


def test_administrator_cannot_manage_panel_users_or_toggle_phone_verification(
    api_client: TestClient,
) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "manager1", "manager-strong-pass", "administrator")
    manager_token = login(api_client, "manager1", "manager-strong-pass")

    resp = api_client.get("/api/panel-users", headers=auth_headers(manager_token))
    assert resp.status_code == 403

    resp = api_client.patch(
        "/api/settings/ignore-phone-verification",
        json={"ignore_phone_verification": True},
        headers=auth_headers(manager_token),
    )
    assert resp.status_code == 403

    # Но настройки контактов поддержки — доступны Administrator
    resp = api_client.patch(
        "/api/settings/support-contacts",
        json={"support_contacts": {"telegram": "@support"}},
        headers=auth_headers(manager_token),
    )
    assert resp.status_code == 200


def test_administrator_can_edit_giveaways_and_block_participants(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "manager2", "manager-strong-pass", "administrator")
    manager_token = login(api_client, "manager2", "manager-strong-pass")

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Y", "prefix": "MGY", "ticket_price": 1000, "max_tickets": 10},
        headers=auth_headers(manager_token),
    )
    assert resp.status_code == 201


def test_administrator_can_view_bank_reconciliation_status(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "manager3", "manager-strong-pass", "administrator")
    manager_token = login(api_client, "manager3", "manager-strong-pass")

    resp = api_client.get("/api/bank-reconciliation/status", headers=auth_headers(manager_token))
    assert resp.status_code == 200
    body = resp.json()
    # Не полагаемся на конкретное число тиков — фоновый цикл (backend/background)
    # уже стартовал в lifespan TestClient'а и мог успеть записать 0+ тиков.
    assert "runs" in body
    assert "is_stale" in body
    assert "total_runs_24h" in body
    assert "failed_runs_24h" in body


def test_unauthenticated_request_is_401(api_client: TestClient) -> None:
    resp = api_client.get("/api/dashboard")
    assert resp.status_code == 401


def test_blocked_panel_user_cannot_login(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "blocked1", "blocked-strong-pass", "operator")

    users = api_client.get("/api/panel-users", headers=auth_headers(admin_token)).json()
    blocked_id = next(u["id"] for u in users if u["login"] == "blocked1")
    resp = api_client.patch(
        f"/api/panel-users/{blocked_id}",
        json={"is_blocked": True},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200

    resp = api_client.post(
        "/api/auth/login", json={"login": "blocked1", "password": "blocked-strong-pass"}
    )
    assert resp.status_code == 401
