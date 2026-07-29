"""API «Продажи»: номер счёта/oversold в ответе списка, просмотр квитанций
(см. ТЗ, DECISIONS.md — квитанции хранятся, не распознаются)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.core.db import Database
from app.models.enums import PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _create_open_giveaway(api_client: TestClient, headers: dict[str, str], **kwargs: object) -> int:
    payload = {"name": "Receipts", "prefix": "RCT", "ticket_price": 1000, "max_tickets": 30}
    payload.update(kwargs)
    resp = api_client.post("/api/giveaways", json=payload, headers=headers)
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def _seed_payment_with_receipt(
    giveaway_id: int,
    participant_id: int,
    *,
    with_receipt: bool,
    content_type: str | None = "image/jpeg",
) -> tuple[int, int | None]:
    db = Database(get_settings())
    with db.session() as session:
        payment = Payment(
            order_id="rcpt-order-1",
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=PaymentProviderType.REQUISITES_QR,
            amount=10000,
            quantity=1,
            payment_number=1,
            status=PaymentStatus.SUCCEEDED,
        )
        session.add(payment)
        session.flush()
        payment_id = payment.id
        receipt_id = None
        if with_receipt:
            tmp_dir = tempfile.mkdtemp()
            file_path = Path(tmp_dir) / "receipt.jpg"
            file_path.write_bytes(b"fake-jpeg-content")
            receipt = PaymentReceipt(
                payment_id=payment_id,
                file_path=str(file_path),
                original_filename="receipt.jpg",
                content_type=content_type,
            )
            session.add(receipt)
            session.flush()
            receipt_id = receipt.id
    db.engine.dispose()
    return payment_id, receipt_id


def test_payments_list_includes_invoice_no_and_receipt_count(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers)
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79991112233",
            "participant_full_name": "Получатель",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    payment_id, receipt_id = _seed_payment_with_receipt(
        giveaway_id, participant_id, with_receipt=True
    )
    assert receipt_id is not None

    resp = api_client.get("/api/payments", params={"order_id": "rcpt-order-1"}, headers=headers)
    resp.raise_for_status()
    item = resp.json()["items"][0]
    assert item["invoice_no"] == "RCT-00001"
    assert item["oversold"] is False
    assert item["receipt_count"] == 1


def test_download_receipt_returns_file_content(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers)
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79991112244",
            "participant_full_name": "Получатель",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    payment_id, receipt_id = _seed_payment_with_receipt(
        giveaway_id, participant_id, with_receipt=True
    )
    assert receipt_id is not None

    resp = api_client.get(
        f"/api/payments/{payment_id}/receipts",
        headers=headers,
    )
    resp.raise_for_status()
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == receipt_id

    file_resp = api_client.get(
        f"/api/payments/{payment_id}/receipts/{receipt_id}/file", headers=headers
    )
    file_resp.raise_for_status()
    assert file_resp.content == b"fake-jpeg-content"
    assert file_resp.headers["content-type"] == "image/jpeg"


def test_download_receipt_guesses_content_type_when_missing(api_client: TestClient) -> None:
    # Квитанции от VK-документов сохраняются без content_type в БД (VK не отдаёт
    # mime_type для вложений-документов, см. channels/vk/handlers.py) — раньше это
    # приводило к application/octet-stream и браузер вместо просмотра скачивал файл.
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers)
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79991112277",
            "participant_full_name": "Получатель",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    payment_id, receipt_id = _seed_payment_with_receipt(
        giveaway_id, participant_id, with_receipt=True, content_type=None
    )
    assert receipt_id is not None

    file_resp = api_client.get(
        f"/api/payments/{payment_id}/receipts/{receipt_id}/file", headers=headers
    )
    file_resp.raise_for_status()
    assert file_resp.headers["content-type"] == "image/jpeg"


def test_download_receipt_404_for_unknown_receipt(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers)
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79991112255",
            "participant_full_name": "Получатель",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    payment_id, _ = _seed_payment_with_receipt(giveaway_id, participant_id, with_receipt=False)

    resp = api_client.get(f"/api/payments/{payment_id}/receipts/999/file", headers=headers)
    assert resp.status_code == 404


def test_payments_receipt_count_zero_when_no_receipt(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers)
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79991112266",
            "participant_full_name": "Получатель",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    _seed_payment_with_receipt(giveaway_id, participant_id, with_receipt=False)

    resp = api_client.get("/api/payments", params={"order_id": "rcpt-order-1"}, headers=headers)
    resp.raise_for_status()
    item = resp.json()["items"][0]
    assert item["receipt_count"] == 0
