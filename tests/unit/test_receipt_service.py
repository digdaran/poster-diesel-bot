"""Тесты сохранения квитанций об оплате (app/services/receipt_service.py, см.
ТЗ, DECISIONS.md — квитанции не распознаются, только хранятся)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.db import Database
from app.models.audit_log import AuditLog
from app.models.enums import AuditActorType, PaymentProviderType
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
from app.services import receipt_service
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select


def make_payment(db: Database) -> int:
    with db.session() as session:
        giveaway = Giveaway(name="Test", prefix="RCP", ticket_price=10000, max_tickets=10)
        session.add(giveaway)
        session.flush()
        pool_svc.open_registration(session, giveaway)
        participant = Participant(phone="79991234567")
        session.add(participant)
        session.flush()
        payment = Payment(
            order_id="order-1",
            participant_id=participant.id,
            giveaway_id=giveaway.id,
            provider=PaymentProviderType.MOCK,
            amount=10000,
            quantity=1,
        )
        session.add(payment)
        session.flush()
        return payment.id


def test_save_receipt_writes_file_and_creates_row(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.receipts_dir = str(tmp_path / "receipts")  # type: ignore[misc]
    payment_id = make_payment(db)

    receipt = receipt_service.save_receipt(
        db,
        settings,
        payment_id=payment_id,
        content=b"fake-jpeg-bytes",
        content_type="image/jpeg",
        original_filename=None,
        telegram_file_id="tg-file-1",
    )

    assert receipt.id is not None
    assert Path(receipt.file_path).exists()
    assert Path(receipt.file_path).read_bytes() == b"fake-jpeg-bytes"
    assert Path(receipt.file_path).suffix == ".jpg"

    with db.session() as session:
        rows = list(
            session.execute(
                select(PaymentReceipt).where(PaymentReceipt.payment_id == payment_id)
            ).scalars()
        )
        assert len(rows) == 1
        assert rows[0].telegram_file_id == "tg-file-1"


def test_save_receipt_uses_original_filename_extension(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.receipts_dir = str(tmp_path / "receipts")  # type: ignore[misc]
    payment_id = make_payment(db)

    receipt = receipt_service.save_receipt(
        db,
        settings,
        payment_id=payment_id,
        content=b"%PDF-fake",
        content_type="application/pdf",
        original_filename="receipt.pdf",
    )

    assert Path(receipt.file_path).suffix == ".pdf"


def test_save_receipt_writes_audit_log(db: Database, settings: Settings, tmp_path: Path) -> None:
    settings.receipts_dir = str(tmp_path / "receipts")  # type: ignore[misc]
    payment_id = make_payment(db)

    receipt = receipt_service.save_receipt(
        db,
        settings,
        payment_id=payment_id,
        content=b"data",
        content_type="image/png",
        original_filename=None,
    )

    with db.session() as session:
        entries = list(
            session.execute(select(AuditLog).where(AuditLog.action == "receipt_uploaded")).scalars()
        )
        assert len(entries) == 1
        assert entries[0].actor_type == AuditActorType.PARTICIPANT
        assert entries[0].entity_id == payment_id
        assert entries[0].details["receipt_id"] == receipt.id


def test_save_receipt_guesses_content_type_from_filename_when_missing(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.receipts_dir = str(tmp_path / "receipts")  # type: ignore[misc]
    payment_id = make_payment(db)

    receipt = receipt_service.save_receipt(
        db,
        settings,
        payment_id=payment_id,
        content=b"%PDF-fake",
        content_type=None,
        original_filename="receipt.pdf",
    )

    # VK-документы никогда не отдают mime_type (см. channels/vk/handlers.py) — без
    # угадывания по расширению файла панель отдавала бы application/octet-stream и
    # браузер вместо просмотра скачивал бы файл.
    assert receipt.content_type == "application/pdf"
    assert Path(receipt.file_path).suffix == ".pdf"


def test_save_receipt_multiple_uploads_for_same_payment(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.receipts_dir = str(tmp_path / "receipts")  # type: ignore[misc]
    payment_id = make_payment(db)

    receipt_service.save_receipt(
        db,
        settings,
        payment_id=payment_id,
        content=b"one",
        content_type="image/jpeg",
        original_filename=None,
    )
    receipt_service.save_receipt(
        db,
        settings,
        payment_id=payment_id,
        content=b"two",
        content_type="image/jpeg",
        original_filename=None,
    )

    with db.session() as session:
        rows = list(
            session.execute(
                select(PaymentReceipt).where(PaymentReceipt.payment_id == payment_id)
            ).scalars()
        )
        assert len(rows) == 2
