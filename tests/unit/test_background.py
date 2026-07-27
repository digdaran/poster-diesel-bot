"""Тесты фоновой сверки платежей и освобождения просроченных резервов
(п.7.5, 9, 20.1 ТЗ). До этого модуля poll_pending_payment/
find_expired_reservation_refs существовали и были покрыты тестами по
отдельности, но их никто не вызывал за пределами тестов — из-за чего
платежи и ручные регистрации оставались в PENDING бессрочно.

`_reconcile_pending_payments` (резервирование "на лету", только для
интернет-эквайринга) не покрыт здесь отдельно после удаления TBank/VTB/Mock
(DECISIONS_LOG.md №44) — тестировать резервирующего провайдера больше нечем,
сама функция остаётся мёртвым, но безвредным кодом. Сверка `requisites_qr`
покрыта отдельно в tests/unit/test_bank_reconciliation.py."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, ManualRegistrationStatus, PanelUserRole
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.services import manual_registration_service as manual_svc
from app.services import ticket_pool_service as pool_svc
from backend import background
from sqlalchemy import select


def make_giveaway(db: Database, *, max_tickets: int = 10, prefix: str = "AUG") -> int:
    with db.session() as session:
        g = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=max_tickets)
        session.add(g)
        session.flush()
        pool_svc.open_registration(session, g)
        return g.id


def make_participant(db: Database, phone: str = "79991234567") -> int:
    with db.session() as session:
        p = Participant(phone=phone)
        session.add(p)
        session.flush()
        return p.id


def make_operator(db: Database, login: str = "operator1") -> int:
    with db.session() as session:
        u = PanelUser(login=login, password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(u)
        session.flush()
        return u.id


def test_reconcile_pending_payments_noop_when_none_pending(
    db: Database, settings: Settings
) -> None:
    background._reconcile_pending_payments(db, settings, now=utcnow())  # не должно падать


def test_release_expired_manual_registrations_cancels_and_frees(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_operator(db)
    outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=4,
        operator_id=operator_id,
        ttl_seconds=settings.manual_reservation_ttl_sec,
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 6

    far_future = utcnow() + dt.timedelta(seconds=settings.manual_reservation_ttl_sec + 100)
    background._release_expired_manual_registrations(db, settings, now=far_future)

    with db.session() as session:
        registration = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert registration.status == ManualRegistrationStatus.CANCELLED
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


@dataclass
class FakeVkChannel:
    allowed_by_user: dict[str, bool] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def is_messages_allowed(self, external_user_id: str) -> bool:
        self.calls.append(external_user_id)
        if external_user_id not in self.allowed_by_user:
            raise RuntimeError(f"unexpected VK API call for {external_user_id}")
        return self.allowed_by_user[external_user_id]


def make_vk_binding(
    db: Database, *, participant_id: int, external_user_id: str, messages_allowed: bool | None
) -> None:
    with db.session() as session:
        session.add(
            ChannelBinding(
                participant_id=participant_id,
                channel=ChannelType.VK,
                external_user_id=external_user_id,
                messages_allowed=messages_allowed,
            )
        )


async def test_reconcile_vk_permissions_noop_without_channel(db: Database) -> None:
    await background._reconcile_vk_permissions(db, None)  # не должно падать


async def test_reconcile_vk_permissions_backfills_unknown_bindings(db: Database) -> None:
    """Основной сценарий бага: VK не всегда шлёт `message_allow` отдельным
    событием, когда участник сам написал сообществу первым — локальный флаг
    остаётся NULL, хотя VK уже разрешает проактивную отправку (DECISIONS_LOG.md
    №61). Сверка должна проставить актуальное значение по живому ответу VK."""
    pid = make_participant(db, phone="79991111111")
    make_vk_binding(db, participant_id=pid, external_user_id="111", messages_allowed=None)
    channel = FakeVkChannel(allowed_by_user={"111": True})

    await background._reconcile_vk_permissions(db, channel)

    with db.session() as session:
        binding = session.execute(
            select(ChannelBinding).where(ChannelBinding.external_user_id == "111")
        ).scalar_one()
        assert binding.messages_allowed is True
    assert channel.calls == ["111"]


async def test_reconcile_vk_permissions_skips_already_known_bindings(db: Database) -> None:
    pid = make_participant(db, phone="79992222222")
    make_vk_binding(db, participant_id=pid, external_user_id="222", messages_allowed=False)
    channel = FakeVkChannel()  # любой вызов -> RuntimeError, привязка не должна запрашиваться

    await background._reconcile_vk_permissions(db, channel)

    assert channel.calls == []
    with db.session() as session:
        binding = session.execute(
            select(ChannelBinding).where(ChannelBinding.external_user_id == "222")
        ).scalar_one()
        assert binding.messages_allowed is False


async def test_reconcile_vk_permissions_channel_error_does_not_block_others(
    db: Database,
) -> None:
    pid1 = make_participant(db, phone="79993333333")
    pid2 = make_participant(db, phone="79994444444")
    make_vk_binding(db, participant_id=pid1, external_user_id="333", messages_allowed=None)
    make_vk_binding(db, participant_id=pid2, external_user_id="444", messages_allowed=None)
    channel = FakeVkChannel(allowed_by_user={"444": True})  # "333" отсутствует -> ошибка

    await background._reconcile_vk_permissions(db, channel)  # не должно падать

    with db.session() as session:
        b333 = session.execute(
            select(ChannelBinding).where(ChannelBinding.external_user_id == "333")
        ).scalar_one()
        b444 = session.execute(
            select(ChannelBinding).where(ChannelBinding.external_user_id == "444")
        ).scalar_one()
        assert b333.messages_allowed is None  # сбой -> остаётся NULL, попробуем в след. тик
        assert b444.messages_allowed is True


def test_release_expired_manual_registrations_leaves_fresh_one_alone(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_operator(db)
    outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=4,
        operator_id=operator_id,
        ttl_seconds=settings.manual_reservation_ttl_sec,
    )
    assert outcome.ok

    background._release_expired_manual_registrations(db, settings, now=utcnow())

    with db.session() as session:
        registration = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert registration.status == ManualRegistrationStatus.PENDING
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 6
