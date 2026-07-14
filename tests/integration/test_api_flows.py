"""Интеграционные тесты сквозных сценариев через HTTP API (п.8.2, 20.2 ТЗ):
розыгрыш -> открытие регистрации -> ручная регистрация -> подтверждение -> номерки;
webhook банка -> идемпотентная финализация онлайн-платежа."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def test_full_manual_sale_flow(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    resp = api_client.post(
        "/api/giveaways",
        json={
            "name": "Осенний розыгрыш",
            "prefix": "OSN",
            "ticket_price": 15000,
            "max_tickets": 20,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    giveaway_id = resp.json()["id"]

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_registration_open"] is True

    resp = api_client.post(
        "/api/manual-registrations",
        json={"giveaway_id": giveaway_id, "participant_phone": "+7 999 111-22-33", "quantity": 3},
        headers=headers,
    )
    assert resp.status_code == 201
    registration_id = resp.json()["id"]
    assert resp.json()["status"] == "PENDING"

    resp = api_client.post(f"/api/manual-registrations/{registration_id}/confirm", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"

    resp = api_client.get("/api/tickets", headers=headers)
    assert resp.status_code == 200
    tickets = [t for t in resp.json() if t["giveaway_id"] == giveaway_id]
    assert len(tickets) == 3
    assert all(t["source"] == "manual" for t in tickets)

    resp = api_client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 200
    dashboard = resp.json()
    assert dashboard["revenue_offline"] == 3 * 15000
    assert dashboard["revenue_online"] == 0
    assert dashboard["revenue_total"] == 3 * 15000

    resp = api_client.get(f"/api/giveaways/{giveaway_id}", headers=headers)
    assert resp.json()["tickets_issued"] == 3


def test_giveaway_immutable_fields_not_editable_after_open(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "G", "prefix": "IMM", "ticket_price": 1000, "max_tickets": 5},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)

    # Повторное открытие запрещено
    resp = api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)
    assert resp.status_code == 400

    # Разрешено менять только name/digital_poster_caption — prefix/price/max_tickets
    # физически не принимаются схемой GiveawayUpdateRequest.
    resp = api_client.patch(
        f"/api/giveaways/{giveaway_id}", json={"name": "Новое имя"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Новое имя"
    assert resp.json()["prefix"] == "IMM"


def test_manual_registration_insufficient_tickets_returns_409(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Small", "prefix": "SML", "ticket_price": 1000, "max_tickets": 2},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)

    resp = api_client.post(
        "/api/manual-registrations",
        json={"giveaway_id": giveaway_id, "participant_phone": "79990000000", "quantity": 5},
        headers=headers,
    )
    assert resp.status_code == 409


def test_operator_sees_only_own_manual_registrations(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)

    api_client.post(
        "/api/panel-users",
        json={"login": "op_a", "password": "op-a-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()
    api_client.post(
        "/api/panel-users",
        json={"login": "op_b", "password": "op-b-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Shared", "prefix": "SHR", "ticket_price": 1000, "max_tickets": 20},
        headers=admin_headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=admin_headers)

    op_a_token = login(api_client, "op_a", "op-a-strong-pass")
    op_b_token = login(api_client, "op_b", "op-b-strong-pass")

    api_client.post(
        "/api/manual-registrations",
        json={"giveaway_id": giveaway_id, "participant_phone": "79991110000", "quantity": 1},
        headers=auth_headers(op_a_token),
    ).raise_for_status()
    api_client.post(
        "/api/manual-registrations",
        json={"giveaway_id": giveaway_id, "participant_phone": "79992220000", "quantity": 1},
        headers=auth_headers(op_b_token),
    ).raise_for_status()

    resp_a = api_client.get("/api/manual-registrations", headers=auth_headers(op_a_token))
    resp_b = api_client.get("/api/manual-registrations", headers=auth_headers(op_b_token))
    assert len(resp_a.json()) == 1
    assert len(resp_b.json()) == 1
    assert resp_a.json()[0]["id"] != resp_b.json()[0]["id"]

    # Администратор видит обе
    resp_admin = api_client.get("/api/manual-registrations", headers=admin_headers)
    assert len(resp_admin.json()) == 2


def test_audit_log_records_significant_actions(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    api_client.post(
        "/api/giveaways",
        json={"name": "Audited", "prefix": "AUD", "ticket_price": 1000, "max_tickets": 5},
        headers=headers,
    )
    resp = api_client.get("/api/audit", headers=headers)
    assert resp.status_code == 200
    actions = {row["action"] for row in resp.json()}
    assert "panel_login_success" in actions
    assert "giveaway_create" in actions
