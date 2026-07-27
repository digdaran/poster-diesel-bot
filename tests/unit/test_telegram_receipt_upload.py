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


class _FakeState:
    """Минимальная замена `aiogram.fsm.context.FSMContext` для юнит-тестов
    хендлеров, вызываемых напрямую (мимо диспетчера aiogram)."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self._data: dict[str, object] = dict(data or {})
        self._state: object = None

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)

    async def update_data(self, **kwargs: object) -> None:
        self._data.update(kwargs)

    async def set_data(self, data: dict[str, object]) -> None:
        self._data = dict(data)

    async def get_state(self) -> object:
        return self._state

    async def set_state(self, state: object) -> None:
        self._state = state

    async def clear(self) -> None:
        self._data = {}
        self._state = None


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
    await handlers_module.on_receipt_upload(message, _FakeState())  # type: ignore[arg-type]

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
    await handlers_module.on_receipt_upload(message, _FakeState())  # type: ignore[arg-type]

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
    await handlers_module.on_receipt_upload(message, _FakeState())  # type: ignore[arg-type]

    called.assert_not_called()
    message.answer.assert_awaited_once()
    assert "Не нашёл" in message.answer.await_args.args[0]


async def test_on_receipt_upload_uses_selected_payment_not_latest_pending(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если участник выбрал конкретный счёт в «Мои покупки» (id в данных
    FSM-состояния), квитанция должна прикрепиться именно к нему — даже если
    существует более новый PENDING-платёж (который иначе был бы выбран как
    "последний")."""
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())
    participant_id = _bind_participant(db, uid=204)
    older_payment_id = _make_pending_payment(db, participant_id=participant_id)
    with db.session() as session:
        giveaway_id = session.get(Payment, older_payment_id).giveaway_id  # type: ignore[union-attr]
        newer_payment = Payment(
            order_id="order-rcpt-2",
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=PaymentProviderType.REQUISITES_QR,
            amount=20000,
            quantity=2,
            payment_number=2,
            status=PaymentStatus.PENDING,
        )
        session.add(newer_payment)
        session.flush()
        newer_payment_id = newer_payment.id
    assert newer_payment_id > older_payment_id

    saved: dict[str, object] = {}

    def _fake_save_receipt(db_arg, settings_arg, **kwargs):  # type: ignore[no-untyped-def]
        saved.update(kwargs)
        return MagicMock(id=3)

    monkeypatch.setattr(receipt_service, "save_receipt", _fake_save_receipt)

    state = _FakeState({"receipt_payment_id": older_payment_id})
    message = _FakeMessage(uid=204, photo=[_FakePhotoSize("tg-file-selected")])
    await handlers_module.on_receipt_upload(message, state)  # type: ignore[arg-type]

    assert saved["payment_id"] == older_payment_id
    assert await state.get_data() == {}
    assert await state.get_state() is None


async def test_on_receipt_upload_selected_payment_no_longer_pending_clears_state(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())
    participant_id = _bind_participant(db, uid=205)
    payment_id = _make_pending_payment(db, participant_id=participant_id)
    with db.session() as session:
        payment = session.get(Payment, payment_id)
        assert payment is not None
        payment.status = PaymentStatus.SUCCEEDED
        session.add(payment)

    called = MagicMock()
    monkeypatch.setattr(receipt_service, "save_receipt", called)

    state = _FakeState({"receipt_payment_id": payment_id})
    message = _FakeMessage(uid=205, photo=[_FakePhotoSize("tg-file-stale")])
    await handlers_module.on_receipt_upload(message, state)  # type: ignore[arg-type]

    called.assert_not_called()
    message.answer.assert_awaited_once()
    assert "уже недоступен" in message.answer.await_args.args[0]
    assert await state.get_data() == {}
