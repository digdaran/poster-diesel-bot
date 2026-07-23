"""Тесты обработчика квитанций Telegram-бота (channels/telegram/handlers.py::
on_receipt_upload) — см. ТЗ, DECISIONS.md: квитанция сохраняется, не
распознаётся, привязывается к текущему неоплаченному счёту участника."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.db import Database
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.services import participant_service, receipt_service
from app.services import ticket_pool_service as pool_svc

from channels.telegram import handlers as handlers_module


class _FakeUser:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.username = "tg_user"


class _FakePhotoSize:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class _FakeDocument:
    def __init__(self, file_id: str, file_name: str | None, mime_type: str | None) -> None:
        self.file_id = file_id
        self.file_name = file_name
        self.mime_type = mime_type


class _FakeMessage:
    def __init__(
        self,
        uid: int,
        *,
        photo: list[_FakePhotoSize] | None = None,
        document: _FakeDocument | None = None,
    ) -> None:
        self.from_user = _FakeUser(uid)
        self.chat = MagicMock(id=uid)
        self.photo = photo
        self.document = document
        self.answer = AsyncMock()


class _FakeFile:
    def __init__(self, file_path: str | None = "path/on/telegram/servers.jpg") -> None:
        self.file_path = file_path


class _FakeBot:
    def __init__(self) -> None:
        self.get_file = AsyncMock(return_value=_FakeFile())

        async def _download(path: str, *, destination: object) -> None:
            destination.write(b"fake-photo-bytes")  # type: ignore[attr-defined]

        self.download_file = AsyncMock(side_effect=_download)


class _FakeChannel:
    def __init__(self) -> None:
        self.bot = _FakeBot()


def _bind_participant(db: Database, *, uid: int, phone: str = "79991234567") -> int:
    with db.session() as session:
        participant = Participant(phone=phone, phone_verified=True)
        session.add(participant)
        session.flush()
        participant_service.confirm_channel_binding(
            session,
            channel=ChannelType.TELEGRAM,
            external_user_id=str(uid),
            phone_raw=phone,
            username="tg_user",
        )
        return participant.id


def _make_pending_payment(db: Database, *, participant_id: int) -> int:
    with db.session() as session:
        giveaway = Giveaway(name="Test", prefix="RCT", ticket_price=10000, max_tickets=10)
        session.add(giveaway)
        session.flush()
        pool_svc.open_registration(session, giveaway)
        payment = Payment(
            order_id="order-rcpt-1",
            participant_id=participant_id,
            giveaway_id=giveaway.id,
            provider=PaymentProviderType.REQUISITES_QR,
            amount=10000,
            quantity=1,
            payment_number=1,
            status=PaymentStatus.PENDING,
        )
        session.add(payment)
        session.flush()
        return payment.id


async def test_on_receipt_upload_photo_attaches_to_pending_payment(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())
    participant_id = _bind_participant(db, uid=201)
    payment_id = _make_pending_payment(db, participant_id=participant_id)

    saved: dict[str, object] = {}

    def _fake_save_receipt(db_arg, settings_arg, **kwargs):  # type: ignore[no-untyped-def]
        saved.update(kwargs)
        return MagicMock(id=1)

    monkeypatch.setattr(receipt_service, "save_receipt", _fake_save_receipt)

    message = _FakeMessage(uid=201, photo=[_FakePhotoSize("tg-file-abc")])
    await handlers_module.on_receipt_upload(message)  # type: ignore[arg-type]

    assert saved["payment_id"] == payment_id
    assert saved["content"] == b"fake-photo-bytes"
    assert saved["content_type"] == "image/jpeg"
    assert saved["telegram_file_id"] == "tg-file-abc"
    message.answer.assert_awaited_once()
    assert "Квитанция получена" in message.answer.await_args.args[0]


async def test_on_receipt_upload_document_uses_original_filename(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())
    participant_id = _bind_participant(db, uid=202, phone="79997654321")
    payment_id = _make_pending_payment(db, participant_id=participant_id)

    saved: dict[str, object] = {}

    def _fake_save_receipt(db_arg, settings_arg, **kwargs):  # type: ignore[no-untyped-def]
        saved.update(kwargs)
        return MagicMock(id=2)

    monkeypatch.setattr(receipt_service, "save_receipt", _fake_save_receipt)

    message = _FakeMessage(
        uid=202,
        document=_FakeDocument("tg-doc-1", "receipt.pdf", "application/pdf"),
    )
    await handlers_module.on_receipt_upload(message)  # type: ignore[arg-type]

    assert saved["payment_id"] == payment_id
    assert saved["original_filename"] == "receipt.pdf"
    assert saved["content_type"] == "application/pdf"


async def test_on_receipt_upload_without_active_payment_replies_politely(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())
    _bind_participant(db, uid=203)  # без активного платежа

    called = MagicMock()
    monkeypatch.setattr(receipt_service, "save_receipt", called)

    message = _FakeMessage(uid=203, photo=[_FakePhotoSize("tg-file-xyz")])
    await handlers_module.on_receipt_upload(message)  # type: ignore[arg-type]

    called.assert_not_called()
    message.answer.assert_awaited_once()
    assert "Не нашёл" in message.answer.await_args.args[0]
