"""Акция «N + M в подарок» через HTTP API (DECISIONS_LOG.md №87): создание коллекции с
условиями, неизменяемость условий, ручная регистрация с подарками, пометка подарочных номеров,
фильтр «подарки не выданы» в продажах."""

from __future__ import annotations

from app.core.db import Database
from app.models.enums import PaymentStatus
from app.models.participant import Participant
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import payment_service
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _admin(api_client: TestClient) -> dict[str, str]:
    return auth_headers(login(api_client, "admin", "admin-strong-pass-123"))


def _create_giveaway(
    api_client: TestClient, headers: dict[str, str], *, max_tickets: int = 20, **promo: int
) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Акция", "prefix": "AKC", "ticket_price": 10000, "max_tickets": max_tickets}
        | promo,
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    giveaway_id: int = resp.json()["id"]
    assert api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).status_code == 200
    return giveaway_id


def test_create_giveaway_with_promo_and_it_is_returned(api_client: TestClient) -> None:
    headers = _admin(api_client)
    gid = _create_giveaway(api_client, headers, promo_buy_n=5, promo_gift_m=1)

    body = api_client.get(f"/api/giveaways/{gid}", headers=headers).json()
    assert body["promo_buy_n"] == 5
    assert body["promo_gift_m"] == 1


def test_create_giveaway_without_promo(api_client: TestClient) -> None:
    headers = _admin(api_client)
    gid = _create_giveaway(api_client, headers)
    body = api_client.get(f"/api/giveaways/{gid}", headers=headers).json()
    assert body["promo_buy_n"] is None
    assert body["promo_gift_m"] is None


def test_create_giveaway_rejects_half_configured_or_invalid_promo(api_client: TestClient) -> None:
    headers = _admin(api_client)
    base = {"name": "X", "prefix": "XXX", "ticket_price": 100, "max_tickets": 10}
    for promo in ({"promo_buy_n": 5}, {"promo_gift_m": 1}, {"promo_buy_n": 0, "promo_gift_m": 1}):
        resp = api_client.post("/api/giveaways", json=base | promo, headers=headers)
        assert resp.status_code == 422, promo


def test_promo_cannot_be_changed_after_creation(api_client: TestClient) -> None:
    headers = _admin(api_client)
    gid = _create_giveaway(api_client, headers, promo_buy_n=5, promo_gift_m=1)

    api_client.patch(
        f"/api/giveaways/{gid}", json={"promo_buy_n": 2, "promo_gift_m": 3}, headers=headers
    )

    body = api_client.get(f"/api/giveaways/{gid}", headers=headers).json()
    assert (body["promo_buy_n"], body["promo_gift_m"]) == (5, 1)


def test_manual_registration_with_promo_issues_marked_gift_tickets(api_client: TestClient) -> None:
    headers = _admin(api_client)
    gid = _create_giveaway(api_client, headers, max_tickets=11, promo_buy_n=5, promo_gift_m=1)

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": gid,
            "participant_phone": "+7 999 111-22-33",
            "participant_full_name": "Иван Иванов",
            "quantity": 10,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    reg = resp.json()
    assert reg["gift_quantity"] == 2
    assert reg["revenue"] == 10 * 10000

    resp = api_client.post(f"/api/manual-registrations/{reg['id']}/confirm", headers=headers)
    assert resp.status_code == 200
    # Тираж 11: 10 платных + 1 подарок влез, второй подарок — вручную.
    assert resp.json()["gifts_not_issued"] == 1

    tickets = [
        t
        for t in api_client.get("/api/tickets", headers=headers).json()["items"]
        if t["giveaway_id"] == gid
    ]
    assert len(tickets) == 11
    assert sum(t["is_gift"] for t in tickets) == 1


def test_sales_filter_by_gifts_not_issued(api_client: TestClient) -> None:
    headers = _admin(api_client)
    gid = _create_giveaway(api_client, headers, max_tickets=5, promo_buy_n=5, promo_gift_m=1)

    db: Database = api_client.app.state.db  # type: ignore[attr-defined]
    with db.session() as session:
        participant = Participant(phone="79991112233")
        session.add(participant)
        session.flush()
        pid = participant.id
    provider = RequisitesQrProvider(
        recipient_name="ИП Тест",
        recipient_inn="770101001770",
        recipient_kpp="",
        personal_acc="40802810000000000001",
        bank_name="Тестбанк",
        bic="044525225",
        corresp_acc="30101810000000000225",
        vat_rate_percent=0,
    )
    created = payment_service.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991112233",
        quantity=5,
    )
    assert created.order_id is not None
    payment_service.finalize_payment(
        db, order_id=created.order_id, new_status=PaymentStatus.SUCCEEDED
    )

    items = api_client.get("/api/payments?gifts_not_issued=true", headers=headers).json()["items"]
    assert [p["id"] for p in items] == [created.payment_id]
    assert items[0]["gift_quantity"] == 1
    assert items[0]["gifts_not_issued"] == 1
    assert items[0]["oversold"] is False

    items = api_client.get("/api/payments?gifts_not_issued=false", headers=headers).json()["items"]
    assert items == []
