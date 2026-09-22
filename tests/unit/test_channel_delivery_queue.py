"""Тесты очереди гарантированной доставки (`app/services/channel_delivery_queue.py`,
DECISIONS_LOG.md №73): немедленная попытка с ретраями, при исчерпании — постановка
в очередь без исключения наружу, докрутка фоновым циклом (`process_pending_deliveries`)
до успеха без ограничения числа попыток."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from app.core.db import Database
from app.models.enums import ChannelType, PendingDeliveryKind, PendingDeliveryStatus
from app.models.participant import Participant
from app.models.pending_channel_delivery import PendingChannelDelivery
from app.services import channel_delivery_queue as queue_svc
from sqlalchemy import select


@dataclass
class FakeChannel:
    fail_times: int = 0
    """Сколько первых вызовов ЛЮБОГО метода должны падать, прежде чем начать
    успевать — 0 значит "всегда успех"."""
    calls: list[tuple[str, tuple, dict]] = field(default_factory=list)
    _call_count: int = 0

    async def _maybe_fail(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))
        self._call_count += 1
        if self._call_count <= self.fail_times:
            raise RuntimeError(f"boom on call {self._call_count}")

    async def send_message(self, external_user_id: str, text: str, **kwargs: object) -> None:
        await self._maybe_fail("send_message", external_user_id, text, **kwargs)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        await self._maybe_fail(
            "deliver_purchase", external_user_id, poster_path=poster_path, codes=codes, intro=intro
        )

    async def send_qr_code(
        self, external_user_id: str, qr_code_payload: str, *, caption: str | None = None
    ) -> None:
        await self._maybe_fail("send_qr_code", external_user_id, qr_code_payload, caption=caption)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_with_retry делает реальный backoff между попытками — не нужен в тестах."""
    monkeypatch.setattr("app.channels.retry.asyncio.sleep", AsyncMock())


def _all_deliveries(db: Database) -> list[PendingChannelDelivery]:
    with db.session() as session:
        return list(session.execute(select(PendingChannelDelivery)).scalars().all())


async def test_send_or_enqueue_succeeds_immediately_without_enqueueing(db: Database) -> None:
    channel = FakeChannel()

    sent = await queue_svc.send_or_enqueue(
        db,
        channel=channel,
        channel_type=ChannelType.TELEGRAM,
        external_user_id="123",
        kind=PendingDeliveryKind.TEXT_MESSAGE,
        payload={"text": "привет"},
    )

    assert sent is True
    assert channel.calls == [("send_message", ("123", "привет"), {})]
    assert _all_deliveries(db) == []


async def test_send_or_enqueue_falls_back_to_queue_after_exhausting_retries(
    db: Database,
) -> None:
    channel = FakeChannel(fail_times=999)  # всегда падает
    with db.session() as session:
        session.add(Participant(phone="79995550000", phone_verified=True))
        session.flush()
        participant_id = session.query(Participant).one().id

    sent = await queue_svc.send_or_enqueue(
        db,
        channel=channel,
        channel_type=ChannelType.VK,
        external_user_id="vk-1",
        kind=PendingDeliveryKind.QR_CODE,
        payload={"qr_code_payload": "ST00012|...", "caption": "оплатите"},
        participant_id=participant_id,
    )

    assert sent is False
    rows = _all_deliveries(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == PendingDeliveryStatus.PENDING
    assert row.channel == ChannelType.VK
    assert row.external_user_id == "vk-1"
    assert row.kind == PendingDeliveryKind.QR_CODE
    assert row.payload == {"qr_code_payload": "ST00012|...", "caption": "оплатите"}
    assert row.participant_id == participant_id
    assert row.attempts == 0  # попытки перед постановкой в очередь не считаются


async def test_send_or_enqueue_succeeds_after_transient_failures(db: Database) -> None:
    """Немедленный ретрай (send_with_retry, 3 попытки) сам справляется с
    временным сбоем — до очереди дело не доходит."""
    channel = FakeChannel(fail_times=2)

    sent = await queue_svc.send_or_enqueue(
        db,
        channel=channel,
        channel_type=ChannelType.TELEGRAM,
        external_user_id="123",
        kind=PendingDeliveryKind.TEXT_MESSAGE,
        payload={"text": "привет"},
    )

    assert sent is True
    assert len(channel.calls) == 3
    assert _all_deliveries(db) == []


async def test_process_pending_deliveries_marks_row_sent_on_success(db: Database) -> None:
    with db.session() as session:
        session.add(
            PendingChannelDelivery(
                channel=ChannelType.TELEGRAM,
                external_user_id="123",
                kind=PendingDeliveryKind.TEXT_MESSAGE,
                payload={"text": "привет"},
                status=PendingDeliveryStatus.PENDING,
            )
        )

    telegram_channel = FakeChannel()
    await queue_svc.process_pending_deliveries(
        db, telegram_channel=telegram_channel, vk_channel=None
    )

    rows = _all_deliveries(db)
    assert len(rows) == 1
    assert rows[0].status == PendingDeliveryStatus.SENT
    assert rows[0].sent_at is not None
    assert rows[0].attempts == 1
    assert telegram_channel.calls == [("send_message", ("123", "привет"), {})]


async def test_process_pending_deliveries_keeps_pending_on_repeated_failure(
    db: Database,
) -> None:
    """Один тик — одна попытка (без внутреннего backoff, интервал между тиками
    сам служит паузой); при неудаче строка остаётся PENDING сколько угодно раз —
    без ограничения числа попыток (явное требование заказчика)."""
    with db.session() as session:
        session.add(
            PendingChannelDelivery(
                channel=ChannelType.VK,
                external_user_id="vk-1",
                kind=PendingDeliveryKind.TEXT_MESSAGE,
                payload={"text": "привет"},
                status=PendingDeliveryStatus.PENDING,
            )
        )

    vk_channel = FakeChannel(fail_times=999)
    for expected_attempts in (1, 2, 3):
        await queue_svc.process_pending_deliveries(db, telegram_channel=None, vk_channel=vk_channel)
        rows = _all_deliveries(db)
        assert rows[0].status == PendingDeliveryStatus.PENDING
        assert rows[0].attempts == expected_attempts
        assert rows[0].last_error == f"boom on call {expected_attempts}"


async def test_process_pending_deliveries_skips_rows_without_configured_channel(
    db: Database,
) -> None:
    """Строка адресована VK, но этот процесс поднят без VK-токена (vk_channel=None) —
    остаётся нетронутой, ждёт тик, где канал уже будет доступен."""
    with db.session() as session:
        session.add(
            PendingChannelDelivery(
                channel=ChannelType.VK,
                external_user_id="vk-1",
                kind=PendingDeliveryKind.TEXT_MESSAGE,
                payload={"text": "привет"},
                status=PendingDeliveryStatus.PENDING,
            )
        )

    await queue_svc.process_pending_deliveries(db, telegram_channel=None, vk_channel=None)

    rows = _all_deliveries(db)
    assert rows[0].status == PendingDeliveryStatus.PENDING
    assert rows[0].attempts == 0


async def test_process_pending_deliveries_dispatches_qr_code_kind(db: Database) -> None:
    with db.session() as session:
        session.add(
            PendingChannelDelivery(
                channel=ChannelType.VK,
                external_user_id="vk-1",
                kind=PendingDeliveryKind.QR_CODE,
                payload={"qr_code_payload": "ST00012|...", "caption": "оплатите"},
                status=PendingDeliveryStatus.PENDING,
            )
        )

    vk_channel = FakeChannel()
    await queue_svc.process_pending_deliveries(db, telegram_channel=None, vk_channel=vk_channel)

    assert vk_channel.calls == [("send_qr_code", ("vk-1", "ST00012|..."), {"caption": "оплатите"})]


async def test_process_pending_deliveries_dispatches_deliver_purchase_kind(db: Database) -> None:
    with db.session() as session:
        session.add(
            PendingChannelDelivery(
                channel=ChannelType.TELEGRAM,
                external_user_id="123",
                kind=PendingDeliveryKind.DELIVER_PURCHASE,
                payload={
                    "poster_path": "/data/posters/1.png",
                    "codes": ["ENT-000001"],
                    "intro": "ok",
                },
                status=PendingDeliveryStatus.PENDING,
            )
        )

    telegram_channel = FakeChannel()
    await queue_svc.process_pending_deliveries(
        db, telegram_channel=telegram_channel, vk_channel=None
    )

    assert telegram_channel.calls == [
        (
            "deliver_purchase",
            ("123",),
            {"poster_path": "/data/posters/1.png", "codes": ["ENT-000001"], "intro": "ok"},
        )
    ]


async def test_process_pending_deliveries_respects_batch_limit(db: Database) -> None:
    with db.session() as session:
        for i in range(3):
            session.add(
                PendingChannelDelivery(
                    channel=ChannelType.TELEGRAM,
                    external_user_id=str(i),
                    kind=PendingDeliveryKind.TEXT_MESSAGE,
                    payload={"text": "привет"},
                    status=PendingDeliveryStatus.PENDING,
                )
            )

    telegram_channel = FakeChannel()
    await queue_svc.process_pending_deliveries(
        db, telegram_channel=telegram_channel, vk_channel=None, batch_limit=2
    )

    statuses = [row.status for row in _all_deliveries(db)]
    assert statuses.count(PendingDeliveryStatus.SENT) == 2
    assert statuses.count(PendingDeliveryStatus.PENDING) == 1
