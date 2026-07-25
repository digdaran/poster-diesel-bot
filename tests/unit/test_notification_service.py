"""Тесты проактивных уведомлений об исходе платежа (см. app/services/notification_service.py,
DECISIONS_LOG.md №43): успех доставляет постер+коды через привязку участника,
отказ шлёт короткое уведомление, отсутствие привязки — тихий no-op. Отдельно —
выбор каналов при нескольких привязках (VK участвует только при разрешении
messages_allowed; при наличии обеих подходящих привязок уведомление уходит в
ОБЕ одновременно, не выбирает одну)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.db import Database
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.giveaway_poster import GiveawayPoster
from app.models.participant import Participant
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import notification_service
from app.services import payment_service as svc
from app.services import ticket_pool_service as pool_svc


def make_provider() -> RequisitesQrProvider:
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


@dataclass
class FakeChannel:
    deliver_purchase_calls: list[dict] = field(default_factory=list)
    send_message_calls: list[tuple[str, str]] = field(default_factory=list)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        self.deliver_purchase_calls.append(
            {
                "external_user_id": external_user_id,
                "poster_path": poster_path,
                "codes": codes,
                "intro": intro,
            }
        )

    async def send_message(self, external_user_id: str, text: str, **kwargs: object) -> None:
        self.send_message_calls.append((external_user_id, text))


def make_giveaway(db: Database, *, max_tickets: int = 10, prefix: str = "NTF") -> int:
    with db.session() as session:
        g = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=max_tickets)
        session.add(g)
        session.flush()
        pool_svc.open_registration(session, g)
        return g.id


def make_participant_with_binding(db: Database, *, phone: str = "79991234567") -> int:
    with db.session() as session:
        p = Participant(phone=phone, phone_verified=True)
        session.add(p)
        session.flush()
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.TELEGRAM,
                external_user_id="123456",
                phone_verified=True,
            )
        )
        session.flush()
        return p.id


def make_participant_without_binding(db: Database, *, phone: str = "79997654321") -> int:
    with db.session() as session:
        p = Participant(phone=phone, phone_verified=False)
        session.add(p)
        session.flush()
        return p.id


async def test_notify_success_delivers_purchase_via_binding(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant_with_binding(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied

    channel = FakeChannel()
    await notification_service.notify_payment_outcome(
        db, finalize, telegram_channel=channel, vk_channel=None
    )

    assert len(channel.deliver_purchase_calls) == 1
    call = channel.deliver_purchase_calls[0]
    assert call["external_user_id"] == "123456"
    assert len(call["codes"]) == 2
    assert not channel.send_message_calls


async def test_notify_success_picks_one_of_uploaded_posters(db: Database) -> None:
    gid = make_giveaway(db, prefix="NTP")
    with db.session() as session:
        session.add(GiveawayPoster(giveaway_id=gid, file_path="/data/posters/1/a.png"))
        session.add(GiveawayPoster(giveaway_id=gid, file_path="/data/posters/1/b.png"))
        session.flush()
    pid = make_participant_with_binding(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied

    channel = FakeChannel()
    await notification_service.notify_payment_outcome(
        db, finalize, telegram_channel=channel, vk_channel=None
    )

    assert len(channel.deliver_purchase_calls) == 1
    poster_path = channel.deliver_purchase_calls[0]["poster_path"]
    assert poster_path in {"/data/posters/1/a.png", "/data/posters/1/b.png"}


async def test_notify_failure_sends_short_message(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant_with_binding(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.FAILED)
    assert finalize.applied

    channel = FakeChannel()
    await notification_service.notify_payment_outcome(
        db, finalize, telegram_channel=channel, vk_channel=None
    )

    assert not channel.deliver_purchase_calls
    assert len(channel.send_message_calls) == 1
    assert channel.send_message_calls[0][0] == "123456"


async def test_notify_without_binding_is_noop(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant_without_binding(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79997654321",
        quantity=1,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied

    channel = FakeChannel()
    await notification_service.notify_payment_outcome(
        db, finalize, telegram_channel=channel, vk_channel=None
    )

    assert not channel.deliver_purchase_calls
    assert not channel.send_message_calls


async def test_notify_noop_when_not_applied(db: Database) -> None:
    """`applied=False` означает повторный webhook/гонку с фоновой сверкой —
    уведомление уже было отправлено при первой финализации, повторно слать не нужно."""
    not_applied = svc.FinalizeOutcome(applied=False)
    channel = FakeChannel()
    await notification_service.notify_payment_outcome(
        db, not_applied, telegram_channel=channel, vk_channel=None
    )
    assert not channel.deliver_purchase_calls
    assert not channel.send_message_calls


async def test_notify_late_success_no_tickets_sends_message_via_binding(db: Database) -> None:
    pid = make_participant_with_binding(db)
    outcome = svc.FinalizeOutcome(applied=False, participant_id=pid, late_success_no_tickets=True)
    channel = FakeChannel()

    await notification_service.notify_late_success_no_tickets(
        db, outcome, telegram_channel=channel, vk_channel=None
    )

    assert len(channel.send_message_calls) == 1
    assert channel.send_message_calls[0][0] == "123456"
    assert not channel.deliver_purchase_calls


async def test_notify_late_success_no_tickets_without_binding_is_noop(db: Database) -> None:
    pid = make_participant_without_binding(db)
    outcome = svc.FinalizeOutcome(applied=False, participant_id=pid, late_success_no_tickets=True)
    channel = FakeChannel()

    await notification_service.notify_late_success_no_tickets(
        db, outcome, telegram_channel=channel, vk_channel=None
    )

    assert not channel.send_message_calls


def make_participant_with_vk_binding(
    db: Database, *, phone: str = "79995551122", messages_allowed: bool | None = True
) -> int:
    with db.session() as session:
        p = Participant(phone=phone, phone_verified=False)
        session.add(p)
        session.flush()
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.VK,
                external_user_id="987654",
                phone_verified=False,
                messages_allowed=messages_allowed,
            )
        )
        session.flush()
        return p.id


async def test_notify_uses_vk_when_only_vk_binding_allowed(db: Database) -> None:
    pid = make_participant_with_vk_binding(db, messages_allowed=True)
    outcome = svc.FinalizeOutcome(applied=False, participant_id=pid, late_success_no_tickets=True)
    vk_channel = FakeChannel()

    await notification_service.notify_late_success_no_tickets(
        db, outcome, telegram_channel=None, vk_channel=vk_channel
    )

    assert len(vk_channel.send_message_calls) == 1
    assert vk_channel.send_message_calls[0][0] == "987654"


async def test_notify_skips_vk_when_messages_not_allowed(db: Database) -> None:
    """VK-разрешение отозвано (`message_deny`, см. DECISIONS_LOG.md #32/#33) —
    проактивно писать нельзя, участник остаётся без уведомления (реактивный
    путь через "Проверить статус оплаты" в боте остаётся рабочим fallback'ом)."""
    pid = make_participant_with_vk_binding(db, messages_allowed=False)
    outcome = svc.FinalizeOutcome(applied=False, participant_id=pid, late_success_no_tickets=True)
    vk_channel = FakeChannel()

    await notification_service.notify_late_success_no_tickets(
        db, outcome, telegram_channel=None, vk_channel=vk_channel
    )

    assert not vk_channel.send_message_calls


async def test_notify_sends_to_both_telegram_and_vk_when_both_bound(db: Database) -> None:
    """По прямому запросу заказчика (DECISIONS_LOG.md №43): участник с обеими
    подходящими привязками получает уведомление в оба канала одновременно,
    а не только в Telegram."""
    with db.session() as session:
        p = Participant(phone="79995559900", phone_verified=True)
        session.add(p)
        session.flush()
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.TELEGRAM,
                external_user_id="tg-1",
                phone_verified=True,
            )
        )
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.VK,
                external_user_id="vk-1",
                phone_verified=False,
                messages_allowed=True,
            )
        )
        session.flush()
        pid = p.id

    outcome = svc.FinalizeOutcome(applied=False, participant_id=pid, late_success_no_tickets=True)
    telegram_channel = FakeChannel()
    vk_channel = FakeChannel()

    await notification_service.notify_late_success_no_tickets(
        db, outcome, telegram_channel=telegram_channel, vk_channel=vk_channel
    )

    assert telegram_channel.send_message_calls == [
        ("tg-1", notification_service._LATE_SUCCESS_NO_TICKETS_TEXT)
    ]
    assert vk_channel.send_message_calls == [
        ("vk-1", notification_service._LATE_SUCCESS_NO_TICKETS_TEXT)
    ]


async def test_notify_channel_failure_does_not_block_other_channel(db: Database) -> None:
    """Если отправка в один канал падает (напр. VK вернул ошибку запрета,
    см. DECISIONS_LOG.md №43), доставка в уже подтверждённый другой канал всё
    равно должна пройти."""
    with db.session() as session:
        p = Participant(phone="79995559911", phone_verified=True)
        session.add(p)
        session.flush()
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.TELEGRAM,
                external_user_id="tg-2",
                phone_verified=True,
            )
        )
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.VK,
                external_user_id="vk-2",
                phone_verified=False,
                messages_allowed=True,
            )
        )
        session.flush()
        pid = p.id

    outcome = svc.FinalizeOutcome(applied=False, participant_id=pid, late_success_no_tickets=True)
    telegram_channel = FakeChannel()

    class FailingChannel(FakeChannel):
        async def send_message(self, external_user_id: str, text: str, **kwargs: object) -> None:
            raise RuntimeError("messages.send forbidden")

    vk_channel = FailingChannel()

    await notification_service.notify_late_success_no_tickets(
        db, outcome, telegram_channel=telegram_channel, vk_channel=vk_channel
    )

    assert telegram_channel.send_message_calls == [
        ("tg-2", notification_service._LATE_SUCCESS_NO_TICKETS_TEXT)
    ]
