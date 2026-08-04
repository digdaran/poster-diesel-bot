"""Тесты аннулирования уже завершённой покупки супер-админом (п.20.1 ТЗ,
DECISIONS.md/DECISIONS_LOG.md №69): перевод SUCCEEDED/CONFIRMED в REFUNDED,
возврат выданных номерков в оборот (issued -> free), удаление выданных
Ticket, идемпотентность/асимметрия отказов между payment_service и
manual_registration_service (та же намеренная асимметрия, что и у
finalize_payment/confirm-cancel, см. ARCHITECTURE.md §4)."""

from __future__ import annotations

import pytest
from app.core.db import Database
from app.models.audit_log import AuditLog
from app.models.enums import ManualRegistrationStatus, PanelUserRole, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.models.ticket_pool import TicketPool
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import manual_registration_service as manual_svc
from app.services import payment_service as svc
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select


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


def make_panel_user(db: Database, login: str = "super1") -> int:
    with db.session() as session:
        user = PanelUser(login=login, password_hash="x", role=PanelUserRole.SUPER_ADMIN)
        session.add(user)
        session.flush()
        return user.id


# --- payment_service.refund_payment ------------------------------------------------


def test_refund_payment_releases_tickets_and_marks_refunded(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    admin_id = make_panel_user(db)
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 7

    refund = svc.refund_payment(
        db, payment_id=outcome.payment_id, reason="Покупатель передумал", panel_user_id=admin_id
    )

    assert refund.applied
    assert refund.current_status == PaymentStatus.REFUNDED
    assert refund.quantity == 3
    assert len(refund.released_codes) == 3
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10  # номерки вернулись в оборот

    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.REFUNDED
        assert payment.refund_reason == "Покупатель передумал"
        assert payment.refunded_by_panel_user_id == admin_id
        assert payment.refunded_at is not None

        # Ticket-строки удалены — иначе повторная выдача того же номера была бы
        # невозможна из-за уникальности (giveaway_id, number)/pool_id.
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert tickets == []

        giveaway = session.get(Giveaway, gid)
        assert giveaway is not None
        assert giveaway.tickets_issued == 0
        assert giveaway.tickets_reserved == 0

        pool_rows = list(
            session.execute(select(TicketPool).where(TicketPool.giveaway_id == gid)).scalars()
        )
        assert all(r.status.value == "free" for r in pool_rows)
        assert all(r.payment_id is None and r.participant_id is None for r in pool_rows)


def test_refund_payment_noop_on_pending_payment(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    admin_id = make_panel_user(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok

    refund = svc.refund_payment(
        db, payment_id=outcome.payment_id, reason="тест", panel_user_id=admin_id
    )

    assert not refund.applied
    assert refund.current_status == PaymentStatus.PENDING
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.PENDING  # не тронут


def test_refund_payment_is_not_reapplicable(db: Database) -> None:
    """Повторный вызов на уже REFUNDED — no-op (applied=False), как и
    cancel_payment/finalize_payment на терминальном статусе."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    admin_id = make_panel_user(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    first = svc.refund_payment(
        db, payment_id=outcome.payment_id, reason="первая причина", panel_user_id=admin_id
    )
    assert first.applied

    second = svc.refund_payment(
        db, payment_id=outcome.payment_id, reason="вторая причина", panel_user_id=admin_id
    )
    assert not second.applied
    assert second.current_status == PaymentStatus.REFUNDED
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.refund_reason == "первая причина"  # не перезаписана вторым вызовом


def test_refunded_ticket_can_be_repurchased_by_another_participant(db: Database) -> None:
    """Сквозной сценарий: аннулированный номерок реально возвращается "в
    оборот" — его может купить другой участник."""
    gid = make_giveaway(db, max_tickets=1, prefix="ONE")
    pid_a = make_participant(db, "79991111111")
    pid_b = make_participant(db, "79992222222")
    admin_id = make_panel_user(db)
    provider = make_provider()

    outcome_a = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid_a,
        participant_phone="79991111111",
        quantity=1,
    )
    assert outcome_a.ok
    finalize_a = svc.finalize_payment(
        db, order_id=outcome_a.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize_a.applied
    first_code = finalize_a.tickets[0].full_code  # type: ignore[index]

    # Пока первый номерок не аннулирован — новый покупатель наткнётся на
    # нехватку (max_tickets=1).
    blocked = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid_b,
        participant_phone="79992222222",
        quantity=1,
    )
    assert not blocked.ok

    svc.refund_payment(
        db, payment_id=outcome_a.payment_id, reason="возврат", panel_user_id=admin_id
    )

    outcome_b = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid_b,
        participant_phone="79992222222",
        quantity=1,
    )
    assert outcome_b.ok
    finalize_b = svc.finalize_payment(
        db, order_id=outcome_b.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize_b.applied
    assert finalize_b.tickets[0].full_code == first_code  # тот же физический номер


# --- manual_registration_service.refund_manual_registration -------------------------


def test_refund_manual_registration_releases_tickets_and_marks_refunded(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_panel_user(db, "operator-refund")
    admin_id = make_panel_user(db, "super-refund")

    create_outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=2,
        operator_id=operator_id,
        ttl_seconds=3600,
    )
    assert create_outcome.ok
    confirm_outcome = manual_svc.confirm_manual_registration(
        db, manual_registration_id=create_outcome.manual_registration_id
    )
    assert len(confirm_outcome.tickets) == 2
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 8

    refund = manual_svc.refund_manual_registration(
        db,
        manual_registration_id=create_outcome.manual_registration_id,
        reason="Ошиблись с количеством",
        panel_user_id=admin_id,
    )

    assert refund.quantity == 2
    assert len(refund.released_codes) == 2
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10

    with db.session() as session:
        registration = session.get(ManualRegistration, create_outcome.manual_registration_id)
        assert registration is not None
        assert registration.status == ManualRegistrationStatus.REFUNDED
        assert registration.refund_reason == "Ошиблись с количеством"
        assert registration.refunded_by_panel_user_id == admin_id
        assert registration.refunded_at is not None

        tickets = list(
            session.execute(
                select(Ticket).where(
                    Ticket.manual_registration_id == create_outcome.manual_registration_id
                )
            ).scalars()
        )
        assert tickets == []

        giveaway = session.get(Giveaway, gid)
        assert giveaway is not None
        assert giveaway.tickets_issued == 0


def test_refund_manual_registration_raises_on_pending(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_panel_user(db, "operator-pending")
    admin_id = make_panel_user(db, "super-pending")

    create_outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=1,
        operator_id=operator_id,
        ttl_seconds=3600,
    )
    assert create_outcome.ok

    with pytest.raises(manual_svc.ManualRegistrationStateError):
        manual_svc.refund_manual_registration(
            db,
            manual_registration_id=create_outcome.manual_registration_id,
            reason="тест",
            panel_user_id=admin_id,
        )


def test_refund_manual_registration_raises_on_repeat(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_panel_user(db, "operator-repeat")
    admin_id = make_panel_user(db, "super-repeat")

    create_outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=1,
        operator_id=operator_id,
        ttl_seconds=3600,
    )
    manual_svc.confirm_manual_registration(
        db, manual_registration_id=create_outcome.manual_registration_id
    )
    manual_svc.refund_manual_registration(
        db,
        manual_registration_id=create_outcome.manual_registration_id,
        reason="первая",
        panel_user_id=admin_id,
    )

    with pytest.raises(manual_svc.ManualRegistrationStateError):
        manual_svc.refund_manual_registration(
            db,
            manual_registration_id=create_outcome.manual_registration_id,
            reason="вторая",
            panel_user_id=admin_id,
        )


def test_refund_payment_does_not_write_audit_itself(db: Database) -> None:
    """Аудит-лог для аннулирования пишет API-слой (backend/api/sales.py), не
    сервис — сервис остаётся переиспользуемым без побочного requirement на
    request-контекст (тот же принцип, что и у cancel_payment/finalize_payment)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    admin_id = make_panel_user(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    svc.refund_payment(db, payment_id=outcome.payment_id, reason="тест", panel_user_id=admin_id)

    with db.session() as session:
        entries = list(
            session.execute(select(AuditLog).where(AuditLog.action == "payment_refund")).scalars()
        )
        assert entries == []
