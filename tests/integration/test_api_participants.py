"""Правка данных участника администратором панели (`PATCH /api/participants/{id}`):
имя, номер телефона (см. DECISIONS_LOG.md №72) — доступно Administrator/Super Admin,
Operator запрещено (`PARTICIPANT_EDIT`, п.11.3 ТЗ)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _create_open_giveaway(
    api_client: TestClient, headers: dict[str, str], *, name: str, prefix: str
) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={"name": name, "prefix": prefix, "ticket_price": 1000, "max_tickets": 30},
        headers=headers,
    )
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def _register_participant(
    api_client: TestClient,
    headers: dict[str, str],
    *,
    giveaway_id: int,
    phone: str,
    full_name: str,
) -> int:
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": phone,
            "participant_full_name": full_name,
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    return int(resp.json()["participant_id"])


def test_update_participant_forbidden_for_operator(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, name="Тест1", prefix="T1")
    participant_id = _register_participant(
        api_client, admin_headers, giveaway_id=giveaway_id, phone="79990005001", full_name="Иван"
    )
    api_client.post(
        "/api/panel-users",
        json={"login": "op_pedit", "password": "op-edit-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()
    op_headers = auth_headers(login(api_client, "op_pedit", "op-edit-strong-pass"))

    resp = api_client.patch(
        f"/api/participants/{participant_id}", json={"phone": "79990005002"}, headers=op_headers
    )
    assert resp.status_code == 403


def test_update_participant_changes_phone(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, name="Тест2", prefix="T2")
    participant_id = _register_participant(
        api_client, admin_headers, giveaway_id=giveaway_id, phone="79990005003", full_name="Пётр"
    )

    resp = api_client.patch(
        f"/api/participants/{participant_id}",
        json={"phone": "+7 999 000-50-04"},
        headers=admin_headers,
    )
    resp.raise_for_status()
    assert resp.json()["phone"] == "79990005004"

    resp = api_client.get(f"/api/participants/{participant_id}", headers=admin_headers)
    resp.raise_for_status()
    assert resp.json()["phone"] == "79990005004"


def test_update_participant_rejects_invalid_phone(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, name="Тест3", prefix="T3")
    participant_id = _register_participant(
        api_client, admin_headers, giveaway_id=giveaway_id, phone="79990005005", full_name="Анна"
    )

    resp = api_client.patch(
        f"/api/participants/{participant_id}", json={"phone": "не номер"}, headers=admin_headers
    )
    assert resp.status_code == 400

    resp = api_client.get(f"/api/participants/{participant_id}", headers=admin_headers)
    resp.raise_for_status()
    assert resp.json()["phone"] == "79990005005"  # не изменился


def test_update_participant_rejects_phone_taken_by_another_participant(
    api_client: TestClient,
) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, name="Тест4", prefix="T4")
    _register_participant(
        api_client, admin_headers, giveaway_id=giveaway_id, phone="79990005006", full_name="Иван"
    )
    other_id = _register_participant(
        api_client, admin_headers, giveaway_id=giveaway_id, phone="79990005007", full_name="Пётр"
    )

    resp = api_client.patch(
        f"/api/participants/{other_id}", json={"phone": "79990005006"}, headers=admin_headers
    )
    assert resp.status_code == 409

    resp = api_client.get(f"/api/participants/{other_id}", headers=admin_headers)
    resp.raise_for_status()
    assert resp.json()["phone"] == "79990005007"  # не изменился
