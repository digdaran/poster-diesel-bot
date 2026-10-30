"""Акция «за каждые N купленных за раз — M в подарок» (DECISIONS_LOG.md №87): расчёт числа
подарков, выдача при подтверждении оплаты/ручной регистрации, частичная выдача на хвосте
тиража, учёт невыданных подарков, аннулирование, конкурентный хвост."""

from __future__ import annotations

import threading

import pytest
from app.core.db import Database
from app.models.enums import PanelUserRole, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import manual_registration_service as manual_svc
from app.services import payment_service as svc
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select


def make_giveaway(
    db: Database,
    *,
    max_tickets: int = 20,
    prefix: str = "PRM",
    buy_n: int | None = 5,
    gift_m: int | None = 1,
) -> int:
    with db.session() as session:
        g = Giveaway(
            name="Promo",
            prefix=prefix,
            ticket_price=10000,
            max_tickets=max_tickets,
            promo_buy_n=buy_n,
            promo_gift_m=gift_m,
        )
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


def make_panel_user(db: Database, role: PanelUserRole = PanelUserRole.SUPER_ADMIN) -> int:
    with db.session() as session:
        u = PanelUser(login=f"u-{role.value}", password_hash="x", role=role)
        session.add(u)
        session.flush()
        return u.id


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


def buy(db: Database, gid: int, pid: int, quantity: int) -> svc.CreatePaymentOutcome:
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=quantity,
    )
    assert outcome.ok
    return outcome


def pay(db: Database, outcome: svc.CreatePaymentOutcome) -> svc.FinalizeOutcome:
    assert outcome.order_id is not None
    return svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)


def load_payment(db: Database, payment_id: int | None) -> Payment:
    with db.session() as session:
        return session.execute(select(Payment).where(Payment.id == payment_id)).scalar_one()


def tickets_of(db: Database, *, payment_id: int | None = None, reg_id: int | None = None) -> list:
    with db.session() as session:
        stmt = select(Ticket)
        stmt = (
            stmt.where(Ticket.payment_id == payment_id)
            if payment_id is not None
            else stmt.where(Ticket.manual_registration_id == reg_id)
        )
        return list(session.execute(stmt).scalars())


def load_giveaway(db: Database, gid: int) -> Giveaway:
    with db.session() as session:
        return session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()


@pytest.mark.parametrize(
    ("quantity", "buy_n", "gift_m", "expected"),
    [
        (12, 5, 1, 2),
        (14, 5, 1, 2),
        (15, 5, 1, 3),
        (4, 5, 1, 0),
        (5, 5, 1, 1),
        (10, 5, 2, 4),
        (12, None, None, 0),
        (0, 5, 1, 0),
    ],
)
def test_gift_quantity_for(
    quantity: int, buy_n: int | None, gift_m: int | None, expected: int
) -> None:
    g = Giveaway(promo_buy_n=buy_n, promo_gift_m=gift_m)
    assert g.gift_quantity_for(quantity) == expected


def test_create_payment_records_gift_quantity_and_charges_only_paid(db: Database) -> None:
    gid = make_giveaway(db)
    outcome = buy(db, gid, make_participant(db), 10)
    payment = load_payment(db, outcome.payment_id)
    assert payment.gift_quantity == 2
    assert payment.quantity == 10
    assert payment.amount == 10 * 10000


def test_finalize_issues_paid_and_gift_tickets(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=20)
    outcome = buy(db, gid, make_participant(db), 10)

    result = pay(db, outcome)

    assert result.applied
    assert len(result.tickets or []) == 10
    assert len(result.gift_tickets or []) == 2
    assert result.gifts_not_issued == 0
    tickets = tickets_of(db, payment_id=outcome.payment_id)
    assert sum(t.is_gift for t in tickets) == 2
    assert len({t.number for t in tickets}) == 12
    g = load_giveaway(db, gid)
    assert g.tickets_issued == 12
    assert g.tickets_reserved == 0
    assert load_payment(db, outcome.payment_id).gifts_not_issued == 0


def test_no_promo_giveaway_issues_no_gifts(db: Database) -> None:
    gid = make_giveaway(db, buy_n=None, gift_m=None)
    outcome = buy(db, gid, make_participant(db), 10)
    result = pay(db, outcome)
    assert len(result.tickets or []) == 10
    assert not result.gift_tickets
    assert load_giveaway(db, gid).tickets_issued == 10


def test_partial_gifts_on_pool_tail(db: Database) -> None:
    """Положено 2 подарка, после платных свободен 1 номер — выдаём 1, второй ждёт ручной
    выдачи. Продажа проходит."""
    gid = make_giveaway(db, max_tickets=11)
    outcome = buy(db, gid, make_participant(db), 10)

    result = pay(db, outcome)

    assert len(result.tickets or []) == 10
    assert len(result.gift_tickets or []) == 1
    assert result.gifts_not_issued == 1
    payment = load_payment(db, outcome.payment_id)
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.oversold is False
    assert payment.gifts_not_issued == 1
    assert load_giveaway(db, gid).tickets_issued == 11


def test_no_room_for_gifts_sale_still_goes_through(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    outcome = buy(db, gid, make_participant(db), 10)

    result = pay(db, outcome)

    assert len(result.tickets or []) == 10
    assert result.gift_tickets == []
    assert result.gifts_not_issued == 2
    payment = load_payment(db, outcome.payment_id)
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.gifts_not_issued == 2
    assert load_giveaway(db, gid).tickets_issued == 10


def test_paid_oversold_marks_all_gifts_not_issued(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=5)
    first = buy(db, gid, make_participant(db), 5)
    second = buy(db, gid, make_participant(db, "79990009999"), 5)
    pay(db, second)  # забирает весь тираж

    result = pay(db, first)

    assert result.late_success_no_tickets
    assert result.gifts_not_issued == 1
    payment = load_payment(db, first.payment_id)
    assert payment.oversold is True
    assert payment.gifts_not_issued == 1


def test_repeated_finalize_does_not_issue_gifts_twice(db: Database) -> None:
    gid = make_giveaway(db)
    outcome = buy(db, gid, make_participant(db), 5)
    pay(db, outcome)

    repeat = pay(db, outcome)

    assert not repeat.applied
    assert len(tickets_of(db, payment_id=outcome.payment_id)) == 6
    assert load_giveaway(db, gid).tickets_issued == 6


def test_late_success_after_failed_also_issues_gifts(db: Database) -> None:
    gid = make_giveaway(db)
    outcome = buy(db, gid, make_participant(db), 5)
    assert outcome.order_id is not None
    svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.FAILED)

    late = pay(db, outcome)

    assert late.applied
    assert len(late.tickets or []) == 5
    assert len(late.gift_tickets or []) == 1


def test_create_payment_checks_only_paid_quantity_against_free(db: Database) -> None:
    """Подарок не влезает уже при создании счёта — счёт всё равно выставляется."""
    gid = make_giveaway(db, max_tickets=5)
    outcome = buy(db, gid, make_participant(db), 5)
    assert load_payment(db, outcome.payment_id).gift_quantity == 1


def test_refund_returns_gift_tickets_to_pool(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=20)
    outcome = buy(db, gid, make_participant(db), 10)
    pay(db, outcome)
    assert outcome.payment_id is not None

    refund = svc.refund_payment(
        db, payment_id=outcome.payment_id, reason="тест", panel_user_id=make_panel_user(db)
    )

    assert refund.applied
    assert len(refund.released_codes) == 12
    assert tickets_of(db, payment_id=outcome.payment_id) == []
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 20
    assert load_giveaway(db, gid).tickets_issued == 0


def _create_registration(db: Database, gid: int, quantity: int) -> int:
    outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=make_participant(db),
        quantity=quantity,
        operator_id=make_panel_user(db, PanelUserRole.OPERATOR),
        ttl_seconds=3600,
    )
    assert outcome.ok
    assert outcome.manual_registration_id is not None
    return outcome.manual_registration_id


def test_manual_registration_gifts_issued_on_confirm(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=20)
    reg_id = _create_registration(db, gid, 10)
    # Подарки не резервируются, пока регистрация не подтверждена.
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10

    confirm = manual_svc.confirm_manual_registration(db, manual_registration_id=reg_id)

    assert len(confirm.tickets) == 10
    assert len(confirm.gift_tickets) == 2
    assert confirm.gifts_not_issued == 0
    assert sum(t.is_gift for t in tickets_of(db, reg_id=reg_id)) == 2
    g = load_giveaway(db, gid)
    assert g.tickets_issued == 12
    assert g.tickets_reserved == 0


def test_manual_registration_partial_gifts(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=11)
    reg_id = _create_registration(db, gid, 10)

    confirm = manual_svc.confirm_manual_registration(db, manual_registration_id=reg_id)

    assert len(confirm.gift_tickets) == 1
    assert confirm.gifts_not_issued == 1
    with db.session() as session:
        reg = session.get(ManualRegistration, reg_id)
        assert reg is not None
        assert reg.gift_quantity == 2
        assert reg.gifts_not_issued == 1


def test_manual_registration_cancel_releases_only_paid_reservation(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=20)
    reg_id = _create_registration(db, gid, 10)
    manual_svc.cancel_manual_registration(db, manual_registration_id=reg_id)
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 20


def test_manual_registration_refund_returns_gifts(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=20)
    reg_id = _create_registration(db, gid, 5)
    manual_svc.confirm_manual_registration(db, manual_registration_id=reg_id)

    manual_svc.refund_manual_registration(
        db, manual_registration_id=reg_id, reason="тест", panel_user_id=make_panel_user(db)
    )

    assert tickets_of(db, reg_id=reg_id) == []
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 20


def test_concurrent_confirmations_on_tail_never_exceed_pool(db: Database) -> None:
    """Несколько покупок по 5 (+1 подарок) одновременно подтверждаются на тираже, где на всех
    не хватает: ни одного дубля номера, выдано не больше тиража, недовыдача учтена."""
    gid = make_giveaway(db, max_tickets=14)
    outcomes = [buy(db, gid, make_participant(db, f"7999000000{i}"), 5) for i in range(3)]
    barrier = threading.Barrier(len(outcomes))
    errors: list[BaseException] = []

    def worker(o: svc.CreatePaymentOutcome) -> None:
        try:
            barrier.wait()
            pay(db, o)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(o,)) for o in outcomes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with db.session() as session:
        tickets = list(session.execute(select(Ticket).where(Ticket.giveaway_id == gid)).scalars())
        payments = list(
            session.execute(select(Payment).where(Payment.giveaway_id == gid)).scalars()
        )
    numbers = [t.number for t in tickets]
    assert len(numbers) == len(set(numbers))
    assert len(numbers) <= 14
    assert load_giveaway(db, gid).tickets_issued == len(numbers)
    # 3 × (5 платных + 1 подарок) = 18 положено: всё, что не выдано, учтено на покупках —
    # платные через oversold (всё-или-ничего), подарки через gifts_not_issued.
    paid_not_issued = sum(p.quantity for p in payments if p.oversold)
    assert len(numbers) + paid_not_issued + sum(p.gifts_not_issued for p in payments) == 18


def test_reports_count_gifts_separately_and_revenue_unchanged(db: Database) -> None:
    from app.services import report_service

    gid = make_giveaway(db, max_tickets=20)
    pay(db, buy(db, gid, make_participant(db), 10))

    with db.session() as session:
        by_source = [
            r
            for r in report_service.tickets_by_giveaway_and_source(session)
            if r["giveaway_id"] == gid
        ]
        by_giveaway = [
            r for r in report_service.revenue_by_giveaway(session) if r["giveaway_id"] == gid
        ]
    assert by_source == [
        {
            "giveaway_id": gid,
            "giveaway_name": "Promo",
            "source": "online",
            "count": 12,
            "gift_count": 2,
        }
    ]
    assert by_giveaway[0]["tickets_issued"] == 12
    assert by_giveaway[0]["gift_tickets_issued"] == 2
    assert by_giveaway[0]["revenue_total"] == 10 * 10000
