"""Удаление чеков архивной коллекции и полное удаление архивной коллекции (DECISIONS_LOG.md
№89): только Super Admin, только из архива, файлы удаляются с диска, участники и аудит
остаются, другие коллекции не затрагиваются."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.models.audit_log import AuditLog
from app.models.enums import PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
from app.models.ticket import Ticket
from app.models.ticket_pool import TicketPool
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import payment_service, poster_service, receipt_service
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.integration.conftest import auth_headers, login

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture
def admin(api_client: TestClient) -> dict[str, str]:
    return auth_headers(login(api_client, "admin", "admin-strong-pass-123"))


@pytest.fixture
def db(api_client: TestClient) -> Database:
    return api_client.app.state.db  # type: ignore[attr-defined, no-any-return]


@pytest.fixture
def file_settings(tmp_path: Path) -> Settings:
    return get_settings().model_copy(
        update={"receipts_dir": str(tmp_path / "receipts"), "poster_dir": str(tmp_path / "posters")}
    )


def _provider() -> RequisitesQrProvider:
    return RequisitesQrProvider(
        recipient_name="ИП Тест",
        recipient_inn="770101001770",
        recipient_kpp="",
        personal_acc="40802810000000000001",
        bank_name="Тестбанк",
        bic="044525225",
        corresp_acc="30101810000000000225",
        vat_rate_percent=0,
    )


def _make_collection(
    api_client: TestClient,
    admin: dict[str, str],
    db: Database,
    settings: Settings,
    *,
    prefix: str,
    phone: str,
    archive: bool = True,
) -> tuple[int, int, list[Path]]:
    """Коллекция с онлайн-оплатой + чеком, ручной регистрацией и постером. Возвращает
    (giveaway_id, participant_id, файлы на диске)."""
    resp = api_client.post(
        "/api/giveaways",
        json={
            "name": f"Коллекция {prefix}",
            "prefix": prefix,
            "ticket_price": 100,
            "max_tickets": 10,
        },
        headers=admin,
    )
    gid: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{gid}/open", headers=admin).raise_for_status()

    with db.session() as session:
        participant = Participant(phone=phone)
        session.add(participant)
        session.flush()
        pid = participant.id
        poster = poster_service.save_poster(
            session,
            settings,
            giveaway_id=gid,
            content=PNG,
            content_type="image/png",
            original_filename="p.png",
        )
        poster_path = Path(poster.file_path)

    created = payment_service.create_payment_safe(
        db, _provider(), giveaway_id=gid, participant_id=pid, participant_phone=phone, quantity=2
    )
    assert created.order_id is not None and created.payment_id is not None
    receipt = receipt_service.save_receipt(
        db,
        settings,
        payment_id=created.payment_id,
        content=b"receipt",
        content_type="image/jpeg",
        original_filename="r.jpg",
    )
    payment_service.finalize_payment(
        db, order_id=created.order_id, new_status=PaymentStatus.SUCCEEDED
    )

    reg = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": gid,
            "participant_phone": phone,
            "participant_full_name": "Покупатель",
            "quantity": 1,
        },
        headers=admin,
    ).json()
    api_client.post(f"/api/manual-registrations/{reg['id']}/confirm", headers=admin)

    if archive:
        api_client.post(f"/api/giveaways/{gid}/close-registration", headers=admin)
        api_client.post(f"/api/giveaways/{gid}/archive", headers=admin).raise_for_status()
    return gid, pid, [Path(receipt.file_path), poster_path]


def _count(db: Database, model: type, *where: object) -> int:
    with db.session() as session:
        return int(
            session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()  # type: ignore[arg-type]
        )


def test_only_super_admin_can_purge(
    api_client: TestClient, admin: dict[str, str], db: Database, file_settings: Settings
) -> None:
    gid, _, _ = _make_collection(
        api_client, admin, db, file_settings, prefix="RA", phone="79991230001"
    )
    for role in ("administrator", "operator"):
        api_client.post(
            "/api/panel-users",
            json={"login": f"u_{role}", "password": "strong-pass-12345", "role": role},
            headers=admin,
        ).raise_for_status()
        headers = auth_headers(login(api_client, f"u_{role}", "strong-pass-12345"))
        assert (
            api_client.get(f"/api/giveaways/{gid}/purge-summary", headers=headers).status_code
            == 403
        )
        assert (
            api_client.post(f"/api/giveaways/{gid}/delete-receipts", headers=headers).status_code
            == 403
        )
        assert api_client.delete(f"/api/giveaways/{gid}", headers=headers).status_code == 403
    assert _count(db, Giveaway, Giveaway.id == gid) == 1


def test_not_archived_collection_cannot_be_purged(
    api_client: TestClient, admin: dict[str, str], db: Database, file_settings: Settings
) -> None:
    gid, _, files = _make_collection(
        api_client, admin, db, file_settings, prefix="RN", phone="79991230002", archive=False
    )
    assert api_client.get(f"/api/giveaways/{gid}/purge-summary", headers=admin).status_code == 409
    assert (
        api_client.post(f"/api/giveaways/{gid}/delete-receipts", headers=admin).status_code == 409
    )
    assert api_client.delete(f"/api/giveaways/{gid}", headers=admin).status_code == 409
    assert all(f.exists() for f in files)
    assert api_client.delete("/api/giveaways/999999", headers=admin).status_code == 404


def test_delete_receipts_removes_files_and_marks_records(
    api_client: TestClient, admin: dict[str, str], db: Database, file_settings: Settings
) -> None:
    gid, _, (receipt_file, poster_file) = _make_collection(
        api_client, admin, db, file_settings, prefix="RR", phone="79991230003"
    )
    summary = api_client.get(f"/api/giveaways/{gid}/purge-summary", headers=admin).json()
    assert summary["receipts"] == 1

    resp = api_client.post(f"/api/giveaways/{gid}/delete-receipts", headers=admin)

    assert resp.status_code == 200
    assert resp.json()["summary"]["receipts"] == 1
    assert resp.json()["file_errors"] == 0
    assert not receipt_file.exists()
    assert not receipt_file.parent.exists()  # пустой каталог счёта тоже убран
    assert poster_file.exists()  # постеры не трогаются
    with db.session() as session:
        receipt = session.execute(select(PaymentReceipt)).scalar_one()
        assert receipt.deleted_at is not None
        payment_id = receipt.payment_id

    payment = next(
        p
        for p in api_client.get("/api/payments", params={"giveaway_id": gid}, headers=admin).json()[
            "items"
        ]
        if p["id"] == payment_id
    )
    assert payment["receipt_count"] == 0
    assert payment["receipt_deleted_count"] == 1
    assert api_client.get(f"/api/payments/{payment_id}/receipts", headers=admin).json() == []
    file_resp = api_client.get(
        f"/api/payments/{payment_id}/receipts/{receipt.id}/file", headers=admin
    )
    assert file_resp.status_code == 410

    # Повтор — ничего не удаляет; коллекция и покупки на месте.
    again = api_client.post(f"/api/giveaways/{gid}/delete-receipts", headers=admin).json()
    assert again["summary"]["receipts"] == 0
    assert _count(db, Payment, Payment.giveaway_id == gid) == 1
    assert _count(db, Ticket, Ticket.giveaway_id == gid) == 3


def test_purge_removes_collection_records_and_files_but_keeps_participants_and_audit(
    api_client: TestClient, admin: dict[str, str], db: Database, file_settings: Settings
) -> None:
    gid, pid, files = _make_collection(
        api_client, admin, db, file_settings, prefix="RP", phone="79991230004"
    )
    other_gid, _, other_files = _make_collection(
        api_client, admin, db, file_settings, prefix="RO", phone="79991230005"
    )

    resp = api_client.delete(f"/api/giveaways/{gid}", headers=admin)

    assert resp.status_code == 200
    assert resp.json()["summary"] == {
        "giveaway_name": "Коллекция RP",
        "prefix": "RP",
        "receipts": 1,
        "payments": 1,
        "manual_registrations": 1,
        "tickets": 3,
        "posters": 1,
    }
    assert api_client.get(f"/api/giveaways/{gid}", headers=admin).status_code == 404
    for model, column in (
        (Ticket, Ticket.giveaway_id),
        (TicketPool, TicketPool.giveaway_id),
        (Payment, Payment.giveaway_id),
        (ManualRegistration, ManualRegistration.giveaway_id),
    ):
        assert _count(db, model, column == gid) == 0
    assert not any(f.exists() for f in files)
    assert _count(db, Participant, Participant.id == pid) == 1
    with db.session() as session:
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "giveaway_purge", AuditLog.entity_id == gid)
        ).scalar_one()
        assert audit.details["tickets"] == 3
        assert _count(db, PaymentReceipt) == 1  # чек другой коллекции

    # Другая коллекция не затронута.
    assert api_client.get(f"/api/giveaways/{other_gid}", headers=admin).status_code == 200
    assert all(f.exists() for f in other_files)
    assert _count(db, Ticket, Ticket.giveaway_id == other_gid) == 3
