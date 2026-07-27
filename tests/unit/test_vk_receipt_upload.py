"""Регресс-тест: VK-ссылки на документы (`doc.url`) отдают 302 на реальный
CDN-URL — `httpx.AsyncClient()` без `follow_redirects=True` не идёт по
редиректу, и `response.raise_for_status()` падает на самом 302 как на ошибке
(`httpx.HTTPStatusError`). Как и в случае с лимитом длины label кнопки
(DECISIONS_LOG.md №58), vkbottle тихо глотает исключение и проваливает
диспетчеризацию в catch-all `on_unhandled_message` — участник вместо
подтверждения квитанции видит «Не понял команду»."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.core.db import Database
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.services import participant_service, receipt_service
from app.services import ticket_pool_service as pool_svc

from channels.vk import handlers as h

_DOC_URL = "https://vk.com/doc111_222?hash=abc"
_CDN_URL = "https://psv4.vkuserphoto.ru/s/v1/d2/real-file.pdf"


class _FakeDoc:
    def __init__(self, url: str, title: str) -> None:
        self.url = url
        self.title = title
        self.owner_id = 111
        self.id = 222


class _FakeAttachment:
    def __init__(self, *, doc: _FakeDoc | None = None) -> None:
        self.photo = None
        self.doc = doc


class _FakeStateDispenser:
    async def get(self, peer_id: int) -> None:
        return None

    async def delete(self, peer_id: int) -> None:
        return None


class _FakeBot:
    def __init__(self) -> None:
        self.state_dispenser = _FakeStateDispenser()


class _FakeChannel:
    def __init__(self) -> None:
        self.bot = _FakeBot()


class _FakeMessage:
    def __init__(self, peer_id: int, attachments: list[_FakeAttachment]) -> None:
        self.peer_id = peer_id
        self.attachments = attachments
        self.answer = AsyncMock()


def _mock_transport() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _DOC_URL:
            return httpx.Response(302, headers={"location": _CDN_URL})
        assert str(request.url) == _CDN_URL
        return httpx.Response(200, content=b"%PDF-fake-receipt-bytes")

    return httpx.MockTransport(_handler)


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _mock_transport()
    real_async_client = httpx.AsyncClient

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _bind_participant(db: Database, *, peer_id: int, phone: str) -> int:
    with db.session() as session:
        participant = Participant(phone=phone, phone_verified=True)
        session.add(participant)
        session.flush()
        participant_service.confirm_channel_binding(
            session,
            channel=ChannelType.VK,
            external_user_id=str(peer_id),
            phone_raw=phone,
            username=None,
        )
        return participant.id


def _make_pending_payment(db: Database, *, participant_id: int) -> int:
    with db.session() as session:
        giveaway = Giveaway(name="Test", prefix="RCT", ticket_price=10000, max_tickets=10)
        session.add(giveaway)
        session.flush()
        pool_svc.open_registration(session, giveaway)
        payment = Payment(
            order_id="order-vk-rcpt-1",
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


async def test_vk_receipt_upload_follows_doc_url_redirect(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(h, "get_channel_db", lambda: db)
    monkeypatch.setattr(h, "_channel", _FakeChannel())
    _patch_httpx_client(monkeypatch)

    participant_id = _bind_participant(db, peer_id=901, phone="79990001122")
    payment_id = _make_pending_payment(db, participant_id=participant_id)

    saved: dict[str, object] = {}

    def _fake_save_receipt(db_arg, settings_arg, **kwargs):  # type: ignore[no-untyped-def]
        saved.update(kwargs)
        return MagicMock(id=1)

    monkeypatch.setattr(receipt_service, "save_receipt", _fake_save_receipt)

    message = _FakeMessage(
        peer_id=901,
        attachments=[_FakeAttachment(doc=_FakeDoc(_DOC_URL, "receipt.pdf"))],
    )
    await h.on_receipt_upload(message)  # type: ignore[arg-type]

    assert saved["payment_id"] == payment_id
    assert saved["content"] == b"%PDF-fake-receipt-bytes"
    assert saved["original_filename"] == "receipt.pdf"
    message.answer.assert_awaited_once()
    assert "Квитанция получена" in message.answer.await_args.args[0]
