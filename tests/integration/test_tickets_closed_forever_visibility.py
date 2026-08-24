"""Раздел «Номера»: Operator не должен видеть номерки/фильтр по коллекциям с
закрытой навсегда (`close-registration`) или заархивированной регистрацией —
Administrator/Super Admin по-прежнему видят всё (нужно для поиска исторических
записей, см. DECISIONS.md/DECISIONS_LOG.md №73 и решение из этой задачи)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login
from tests.integration.test_api_permissions import create_panel_user


def _create_and_open_giveaway(
    api_client: TestClient, headers: dict[str, str], *, prefix: str
) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={
            "name": f"Коллекция {prefix}",
            "prefix": prefix,
            "ticket_price": 1000,
            "max_tickets": 10,
        },
        headers=headers,
    )
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def _issue_manual_ticket(api_client: TestClient, headers: dict[str, str], giveaway_id: int) -> None:
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "+7 999 222-33-44",
            "participant_full_name": "Тест Тестов",
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    registration_id = resp.json()["id"]
    api_client.post(
        f"/api/manual-registrations/{registration_id}/confirm", headers=headers
    ).raise_for_status()


def test_operator_does_not_see_tickets_or_filter_option_for_closed_forever_giveaway(
    api_client: TestClient,
) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    create_panel_user(api_client, admin_token, "op-tickets-clf", "operator-strong-pass", "operator")
    op_headers = auth_headers(login(api_client, "op-tickets-clf", "operator-strong-pass"))

    open_id = _create_and_open_giveaway(api_client, admin_headers, prefix="TCO")
    closed_id = _create_and_open_giveaway(api_client, admin_headers, prefix="TCC")
    _issue_manual_ticket(api_client, admin_headers, open_id)
    _issue_manual_ticket(api_client, admin_headers, closed_id)
    api_client.post(
        f"/api/giveaways/{closed_id}/close-registration", headers=admin_headers
    ).raise_for_status()

    # Не выбирая коллекцию явно ("Все коллекции") — Operator видит только открытую.
    resp = api_client.get("/api/tickets", headers=op_headers)
    assert resp.status_code == 200
    giveaway_ids = {t["giveaway_id"] for t in resp.json()["items"]}
    assert open_id in giveaway_ids
    assert closed_id not in giveaway_ids

    # Список коллекций для select-фильтра тоже не содержит закрытую навсегда.
    resp = api_client.get("/api/giveaways", headers=op_headers)
    assert resp.status_code == 200
    listed_ids = {g["id"] for g in resp.json()}
    assert open_id in listed_ids
    assert closed_id in listed_ids  # /giveaways сам по себе не фильтрует — фильтрует фронтенд

    # Даже явный запрос по id закрытой коллекции ничего не возвращает Operator'у.
    resp = api_client.get("/api/tickets", params={"giveaway_id": closed_id}, headers=op_headers)
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    # Administrator по-прежнему видит номерки обеих коллекций.
    resp = api_client.get("/api/tickets", headers=admin_headers)
    assert resp.status_code == 200
    admin_giveaway_ids = {t["giveaway_id"] for t in resp.json()["items"]}
    assert {open_id, closed_id} <= admin_giveaway_ids
