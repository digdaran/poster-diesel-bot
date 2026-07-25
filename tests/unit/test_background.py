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

from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ManualRegistrationStatus, PanelUserRole
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
