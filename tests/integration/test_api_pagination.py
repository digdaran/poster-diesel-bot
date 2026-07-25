"""Пагинация и фильтры по столбцам для Продажи/Номерки/Ручные регистрации/
Участники (запрос владельца: 5000 участников/20000 номерков/150 розыгрышей —
списки не должны отдаваться целиком)."""

from __future__ import annotations

import datetime as dt

from app.core.config import get_settings
from app.core.db import Database
from app.models.enums import PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _seed_payments(giveaway_id: int, participant_id: int) -> None:
    """Платежи создаются ботом/банком, у панели нет HTTP-эндпоинта для этого —
    сеем напрямую через ORM (тот же паттерн, что tests/unit/test_reports.py)."""
    db = Database(get_settings())
    with db.session() as session:
        session.add(
            Payment(
                order_id="pg-mock-1",
                participant_id=participant_id,
                giveaway_id=giveaway_id,
                provider=PaymentProviderType.MOCK,
                amount=10000,
                quantity=1,
                status=PaymentStatus.SUCCEEDED,
            )
        )
        session.add(
            Payment(
                order_id="pg-tbank-1",
                participant_id=participant_id,
                giveaway_id=giveaway_id,
                provider=PaymentProviderType.TBANK,
                amount=20000,
                quantity=2,
                status=PaymentStatus.SUCCEEDED,
            )
        )
    db.engine.dispose()


def _create_open_giveaway(api_client: TestClient, headers: dict[str, str], **kwargs: object) -> int:
    payload = {"name": "Pagination", "prefix": "PG1", "ticket_price": 1000, "max_tickets": 30}
    payload.update(kwargs)
    resp = api_client.post("/api/giveaways", json=payload, headers=headers)
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def test_tickets_pagination_pages_through_results(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PGT")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990001111",
            "participant_full_name": "Постранично Иванов",
            "quantity": 15,
        },
        headers=headers,
    )
    resp.raise_for_status()
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=headers
    ).raise_for_status()

    resp = api_client.get("/api/tickets", params={"page": 1, "page_size": 10}, headers=headers)
    body = resp.json()
    assert body["total"] == 15
    assert len(body["items"]) == 10
    assert body["page"] == 1
    assert body["page_size"] == 10

    resp = api_client.get("/api/tickets", params={"page": 2, "page_size": 10}, headers=headers)
    body = resp.json()
    assert body["total"] == 15
    assert len(body["items"]) == 5

    resp = api_client.get("/api/tickets", params={"page": 3, "page_size": 10}, headers=headers)
    body = resp.json()
    assert body["total"] == 15
    assert body["items"] == []


def test_tickets_page_size_rejects_arbitrary_values(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.get("/api/tickets", params={"page_size": 37}, headers=headers)
    assert resp.status_code == 422


def test_tickets_export_ignores_pagination_but_respects_filters(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PGE")
    other_giveaway_id = _create_open_giveaway(api_client, headers, prefix="PGO")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990002222",
            "participant_full_name": "Экспорт Петров",
            "quantity": 15,
        },
        headers=headers,
    )
    resp.raise_for_status()
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=headers
    ).raise_for_status()
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": other_giveaway_id,
            "participant_phone": "79990003333",
            "participant_full_name": "Другой Розыгрыш",
            "quantity": 2,
        },
        headers=headers,
    )
    resp.raise_for_status()
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=headers
    ).raise_for_status()

    # page_size=10 бы отдал только часть, но export должен вернуть все 15
    # строк отфильтрованного розыгрыша, не всю таблицу (17) и не одну страницу.
    resp = api_client.get(
        "/api/tickets",
        params={"giveaway_id": giveaway_id, "page": 1, "page_size": 10, "export": "csv"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    csv_lines = [line for line in resp.text.strip().split("\n") if line]
    assert len(csv_lines) - 1 == 15  # минус строка заголовка


def test_manual_registrations_filter_by_giveaway_and_participant_query(
    api_client: TestClient,
) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    g1 = _create_open_giveaway(api_client, headers, prefix="MRF1")
    g2 = _create_open_giveaway(api_client, headers, prefix="MRF2")

    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": g1,
            "participant_phone": "79990004444",
            "participant_full_name": "Найди Меня Пожалуйста",
            "quantity": 1,
        },
        headers=headers,
    ).raise_for_status()
    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": g2,
            "participant_phone": "79990005555",
            "participant_full_name": "Совсем Другой Человек",
            "quantity": 1,
        },
        headers=headers,
    ).raise_for_status()

    resp = api_client.get("/api/manual-registrations", params={"giveaway_id": g1}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["giveaway_id"] == g1

    resp = api_client.get(
        "/api/manual-registrations", params={"participant_query": "Найди"}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["participant_full_name"] == "Найди Меня Пожалуйста"

    resp = api_client.get(
        "/api/manual-registrations", params={"participant_query": "Никого Нет"}, headers=headers
    )
    assert resp.json()["total"] == 0


def test_payments_filter_by_provider_and_order_id(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PAY")
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990006666",
            "participant_full_name": "Плательщик",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    _seed_payments(giveaway_id, participant_id)

    resp = api_client.get("/api/payments", params={"provider": "tbank"}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == "pg-tbank-1"

    resp = api_client.get("/api/payments", params={"order_id": "mock"}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["provider"] == "mock"

    future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    resp = api_client.get("/api/payments", params={"created_from": future}, headers=headers)
    assert resp.json()["total"] == 0


def test_participants_filter_by_blocked_and_verified(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PRT")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990007777",
            "participant_full_name": "Будет Заблокирован",
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    participant_id = resp.json()["participant_id"]
    api_client.post(f"/api/participants/{participant_id}/block", headers=headers).raise_for_status()

    resp = api_client.get("/api/participants", params={"is_blocked": True}, headers=headers)
    body = resp.json()
    assert body["total"] >= 1
    assert all(p["is_blocked"] for p in body["items"])
    assert any(p["id"] == participant_id for p in body["items"])

    resp = api_client.get(
        "/api/participants",
        params={"is_blocked": False, "q": "Будет Заблокирован"},
        headers=headers,
    )
    assert resp.json()["total"] == 0  # заблокирован, под этим фильтром не пройдёт
