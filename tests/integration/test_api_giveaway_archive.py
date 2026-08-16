"""Архивация коллекции (`POST /api/giveaways/{id}/archive` и `.../unarchive`,
см. DECISIONS_LOG.md): мягкое скрытие из раздела «Коллекции» в «Архив», без
удаления связанных Ticket/Payment/ManualRegistration — доступно только Super
Admin, только когда регистрация закрыта навсегда и нет ни одной PENDING
заявки."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.db import Database
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import payment_service
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _create_giveaway(
    api_client: TestClient, headers: dict[str, str], *, prefix: str, name: str = "Archive test"
) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={"name": name, "prefix": prefix, "ticket_price": 1000, "max_tickets": 30},
        headers=headers,
    )
    resp.raise_for_status()
    return int(resp.json()["id"])


def _create_open_giveaway(api_client: TestClient, headers: dict[str, str], *, prefix: str) -> int:
    giveaway_id = _create_giveaway(api_client, headers, prefix=prefix)
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def _close_registration(api_client: TestClient, headers: dict[str, str], giveaway_id: int) -> None:
    api_client.post(
        f"/api/giveaways/{giveaway_id}/close-registration", headers=headers
    ).raise_for_status()


def _administrator_headers(api_client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    api_client.post(
        "/api/panel-users",
        json={
            "login": "mgr_archive",
            "password": "mgr-archive-strong-pass",
            "role": "administrator",
        },
        headers=admin_headers,
    ).raise_for_status()
    token = login(api_client, "mgr_archive", "mgr-archive-strong-pass")
    return auth_headers(token)


def test_archive_forbidden_for_administrator(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    manager_headers = _administrator_headers(api_client, admin_headers)
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="ARF")
    _close_registration(api_client, admin_headers, giveaway_id)

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/archive", headers=manager_headers)
    assert resp.status_code == 403


def test_archive_rejected_while_registration_open(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="ARO")

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/archive", headers=admin_headers)
    assert resp.status_code == 409
    assert "закрыта навсегда" in resp.json()["detail"]


def test_archive_rejected_with_pending_manual_registration(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="ARP")
    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990009001",
            "participant_full_name": "Висящий Покупатель",
            "quantity": 1,
        },
        headers=admin_headers,
    ).raise_for_status()
    _close_registration(api_client, admin_headers, giveaway_id)

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/archive", headers=admin_headers)
    assert resp.status_code == 409
    assert "незавершённые заявки" in resp.json()["detail"]


def test_archive_rejected_with_pending_online_payment(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="ARQ")

    reg_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990009002",
            "participant_full_name": "Онлайн Покупатель",
            "quantity": 1,
        },
        headers=admin_headers,
    )
    reg_resp.raise_for_status()
    participant_id = reg_resp.json()["participant_id"]

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
    db = Database(get_settings())
    outcome = payment_service.create_payment_safe(
        db,
        provider,
        giveaway_id=giveaway_id,
        participant_id=participant_id,
        participant_phone="79990009002",
        quantity=1,
    )
    db.engine.dispose()
    assert outcome.ok

    _close_registration(api_client, admin_headers, giveaway_id)

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/archive", headers=admin_headers)
    assert resp.status_code == 409
    assert "незавершённые заявки" in resp.json()["detail"]


def test_archive_succeeds_and_is_reversible(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="ARS")
    _close_registration(api_client, admin_headers, giveaway_id)

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/archive", headers=admin_headers)
    resp.raise_for_status()
    body = resp.json()
    assert body["is_archived"] is True
    assert body["archived_at"] is not None

    # По умолчанию (без фильтра) архивная коллекция всё ещё видна — её продолжают
    # видеть select-фильтры на других страницах (Продажи/Номера/Отчёты).
    all_ids = {g["id"] for g in api_client.get("/api/giveaways", headers=admin_headers).json()}
    assert giveaway_id in all_ids

    active_ids = {
        g["id"]
        for g in api_client.get(
            "/api/giveaways", params={"is_archived": "false"}, headers=admin_headers
        ).json()
    }
    assert giveaway_id not in active_ids

    archived_ids = {
        g["id"]
        for g in api_client.get(
            "/api/giveaways", params={"is_archived": "true"}, headers=admin_headers
        ).json()
    }
    assert giveaway_id in archived_ids

    audit_resp = api_client.get(
        "/api/audit",
        params={"action": "giveaway_archive", "entity_type": "giveaway"},
        headers=admin_headers,
    )
    audit_resp.raise_for_status()
    audit_items = audit_resp.json()["items"]
    assert any(entry["entity_id"] == giveaway_id for entry in audit_items)

    # Обратимо.
    unarchive_resp = api_client.post(
        f"/api/giveaways/{giveaway_id}/unarchive", headers=admin_headers
    )
    unarchive_resp.raise_for_status()
    unarchived = unarchive_resp.json()
    assert unarchived["is_archived"] is False
    assert unarchived["archived_at"] is None


def test_archive_giveaway_not_found(api_client: TestClient) -> None:
    admin_headers = auth_headers(login(api_client, "admin", "admin-strong-pass-123"))
    resp = api_client.post("/api/giveaways/999999/archive", headers=admin_headers)
    assert resp.status_code == 404
