"""Тесты платежей и идемпотентности (п.7.6, 9, 20.1 ТЗ):
повторная финализация, отказ в оплате, гонка отмены/финализации, поздняя
оплата после отмены/просрочки, резервирование номеров.

Единственный провайдер — `RequisitesQrProvider` (интернет-эквайринг удалён,
см. DECISIONS_LOG.md №44): резервирования "на лету" при создании платежа нет
(`reserves_tickets_on_create=False`), номерки выдаются по факту подтверждения
через `_reserve_and_issue_now` — см. `app/services/payment_service.py`."""

from __future__ import annotations

import threading

from app.core.db import Database
from app.models.audit_log import AuditLog
from app.models.enums import ChannelType, PanelUserRole, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import manual_registration_service as manual_svc
from app.services import participant_service
from app.services import payment_service as svc
from app.services import ticket_pool_service as pool_svc
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


def make_provider() -> RequisitesQrProvider:
    """Провайдер для тестов сервисного слоя — реальный `RequisitesQrProvider`
    с тестовыми (но валидными по форме) реквизитами, без сети (`create_payment`
    — чистая сборка QR-payload, см. app/payments/requisites_qr.py)."""
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


def test_pending_ticket_quantity_zero_without_purchases(db: Database) -> None:
    pid = make_participant(db)
    with db.session() as session:
        assert participant_service.pending_ticket_quantity(session, participant_id=pid) == 0


def test_pending_ticket_quantity_sums_multiple_payments(db: Database) -> None:
    gid_a = make_giveaway(db, max_tickets=10, prefix="AAA")
    gid_b = make_giveaway(db, max_tickets=10, prefix="BBB")
    pid = make_participant(db)
    provider = make_provider()
    svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid_a,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid_b,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    with db.session() as session:
        assert participant_service.pending_ticket_quantity(session, participant_id=pid) == 5


def test_pending_ticket_quantity_sums_payments_and_manual_registrations(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    with db.session() as session:
        operator = PanelUser(login="op-pending-qty", password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(operator)
        session.flush()
        operator_id = operator.id

    svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=4,
        operator_id=operator_id,
        ttl_seconds=3600,
    )
    with db.session() as session:
        assert participant_service.pending_ticket_quantity(session, participant_id=pid) == 7


def test_create_payment_persists_external_payment_id(db: Database) -> None:
    """Регресс на боевой инцидент (см. DECISIONS.md): без сохранённого
    `external_payment_id` резервная проверка статуса невозможна вообще."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    assert outcome.created is not None
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.external_payment_id == outcome.created.external_payment_id
        assert payment.external_payment_id is not None


def test_create_payment_persists_channel(db: Database) -> None:
    """Канал (Telegram/VK), из которого создан платёж, сохраняется на Payment —
    нужен для отображения в панели/отчётах. Отсутствие channel= (например, из
    старого кода вызова) оставляет поле NULL, а не ломает создание платежа."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    with_channel = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
        channel=ChannelType.TELEGRAM,
    )
    assert with_channel.ok
    pid_2 = make_participant(db, phone="79997654321")
    without_channel = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid_2,
        participant_phone="79997654321",
        quantity=1,
    )
    assert without_channel.ok
    with db.session() as session:
        payment_with = session.execute(
            select(Payment).where(Payment.id == with_channel.payment_id)
        ).scalar_one()
        payment_without = session.execute(
            select(Payment).where(Payment.id == without_channel.payment_id)
        ).scalar_one()
        assert payment_with.channel == ChannelType.TELEGRAM
        assert payment_without.channel is None


def test_create_payment_persists_initiating_external_user_id(db: Database) -> None:
    """`initiating_external_user_id` (чат, из которого создан платёж) сохраняется
    на Payment и попадает в `FinalizeOutcome` при финализации — используется
    `notification_service` как fallback-получатель уведомления, когда у
    участника-получателя нет `ChannelBinding` (см. DECISIONS_LOG.md №52)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
        channel=ChannelType.TELEGRAM,
        initiating_external_user_id="buyer-chat-1",
    )
    assert outcome.ok
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.initiating_external_user_id == "buyer-chat-1"

    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied
    assert finalize.initiating_channel == ChannelType.TELEGRAM
    assert finalize.initiating_external_user_id == "buyer-chat-1"


def test_create_payment_persists_payment_url_and_qr(db: Database) -> None:
    """payment_url/qr_code_payload — one-shot данные от провайдера (см.
    CreatedPayment) — должны сохраняться на Payment, а не оставаться только в
    транзиентном outcome (см. DECISIONS.md). RequisitesQrProvider не имеет
    ссылки на оплату (только статический QR) — payment_url всегда None."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    assert outcome.payment_id is not None
    assert outcome.created is not None
    with db.session() as session:
        payment = session.get(Payment, outcome.payment_id)
        assert payment is not None
        assert payment.payment_url == outcome.created.payment_url
        assert payment.payment_url is None
        assert payment.qr_code_payload == outcome.created.qr_code_payload
        assert payment.qr_code_payload is not None  # RequisitesQrProvider всегда отдаёт QR


def test_create_payment_insufficient_tickets_not_created(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=2)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=5,
    )
    assert not outcome.ok
    assert outcome.free_count == 2
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 0  # платёж не создан вовсе


def test_create_payment_blocked_when_pending_limit_exceeded(db: Database) -> None:
    """Лимит суммарного количества экземпляров во всех PENDING-покупках участника
    (DECISIONS_LOG.md №45, отменяет бинарное правило №22) — занижаем лимит для теста,
    чтобы не гонять его на дефолтном значении (20)."""
    db.settings.max_pending_tickets_per_participant = 1
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = make_provider()
    first = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert first.ok

    second = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert not second.ok
    assert second.pending_limit_exceeded
    assert second.pending_quantity == 1
    assert second.pending_limit == 1
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 1  # второй платёж не создан


def test_create_payment_allowed_for_other_giveaway_under_limit(db: Database) -> None:
    """По прямому запросу заказчика (DECISIONS_LOG.md №45): участник с незавершённой
    покупкой на одном розыгрыше может купить и на другом, пока суммарное
    количество не превышает лимит."""
    gid_a = make_giveaway(db, max_tickets=10, prefix="AAA")
    gid_b = make_giveaway(db, max_tickets=10, prefix="BBB")
    pid = make_participant(db)
    provider = make_provider()
    first = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid_a,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert first.ok

    second = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid_b,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert second.ok
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 2


def test_create_payment_rejects_blocked_participant(db: Database) -> None:
    """Регресс: Participant.is_blocked, устанавливаемый через панель, не проверялся
    ни в create_payment, ни в боте — заблокированный участник мог покупать."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    with db.session() as session:
        participant = session.get(Participant, pid)
        assert participant is not None
        participant.is_blocked = True

    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert not outcome.ok
    assert outcome.participant_blocked
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 0


def test_create_payment_blocked_by_existing_pending_manual_registration(db: Database) -> None:
    db.settings.max_pending_tickets_per_participant = 1
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    with db.session() as session:
        operator = PanelUser(login="op1", password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(operator)
        session.flush()
        operator_id = operator.id

    manual_outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=1,
        operator_id=operator_id,
        ttl_seconds=3600,
    )
    assert manual_outcome.ok

    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert not outcome.ok
    assert outcome.pending_limit_exceeded
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 0


def test_create_payment_concurrent_same_participant_only_one_succeeds(db: Database) -> None:
    """Гонка: участник одновременно пытается купить в двух разных розыгрышах, а
    лимит ожидающих экземпляров позволяет только одну из двух покупок — ровно
    одна должна пройти (атомарность проверки лимита, см. DECISIONS_LOG.md №45)."""
    db.settings.max_pending_tickets_per_participant = 1
    gid_a = make_giveaway(db, max_tickets=10, prefix="AAA")
    gid_b = make_giveaway(db, max_tickets=10, prefix="BBB")
    pid = make_participant(db)
    provider = make_provider()

    outcomes: dict[str, svc.CreatePaymentOutcome] = {}
    barrier = threading.Barrier(2)

    def worker(name: str, gid: int) -> None:
        barrier.wait()
        outcomes[name] = svc.create_payment_safe(
            db,
            provider,
            giveaway_id=gid,
            participant_id=pid,
            participant_phone="79991234567",
            quantity=1,
        )

    t1 = threading.Thread(target=worker, args=("A", gid_a))
    t2 = threading.Thread(target=worker, args=("B", gid_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [o for o in outcomes.values() if o.ok]
    blocked = [o for o in outcomes.values() if not o.ok and o.pending_limit_exceeded]
    assert len(successes) == 1
    assert len(blocked) == 1
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 1


def test_finalize_success_issues_tickets(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok
    result = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    assert result.applied
    assert len(result.tickets or []) == 3
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.SUCCEEDED
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) == 3
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        assert g.tickets_issued == 3
        assert g.tickets_reserved == 0


def test_finalize_failed_does_not_touch_pool(db: Database) -> None:
    """Без резервирования "на лету" неудачный платёж не должен был ничего
    занимать в пуле — FAILED не обязан ничего "освобождать"."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=4,
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10
    result = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.FAILED)
    assert result.applied
    assert result.tickets == []
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


def test_repeated_finalize_is_noop(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    first = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    assert first.applied
    second = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    assert not second.applied  # повторная финализация — no-op, без повторной выдачи
    with db.session() as session:
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) == 2  # не удвоилось


def test_finalize_unknown_order_id_is_noop(db: Database) -> None:
    result = svc.finalize_payment(db, order_id="does-not-exist", new_status=PaymentStatus.SUCCEEDED)
    assert not result.applied


def test_finalize_concurrent_calls_only_one_applies(db: Database) -> None:
    """Гонка: две стороны одновременно финализируют один платёж (напр. фоновая
    сверка и ручная проверка участником) — ровно одна выполняет переход,
    вторая получает no-op (п.7.6 ТЗ)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok

    results: list[svc.FinalizeOutcome] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        results.append(
            svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    applied = [r for r in results if r.applied]
    noop = [r for r in results if not r.applied]
    assert len(applied) == 1
    assert len(noop) == 1

    with db.session() as session:
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) == 2  # выдано ровно один раз


def test_cancel_payment_sets_cancelled(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
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

    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)

    assert cancel_outcome.applied
    assert cancel_outcome.current_status == PaymentStatus.CANCELLED
    assert cancel_outcome.late_success_outcome is None
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.CANCELLED
        assert payment.cancelled_at is not None


def test_cancel_payment_noop_on_already_terminal_payment(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
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

    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)

    assert not cancel_outcome.applied
    assert cancel_outcome.current_status == PaymentStatus.SUCCEEDED
    assert cancel_outcome.late_success_outcome is None


def test_cancel_payment_concurrent_with_finalize_only_one_applies(db: Database) -> None:
    """Гонка: cancel_payment и finalize_payment(SUCCEEDED) для одного платежа
    одновременно — атомарный условный UPDATE гарантирует, что выигрывает
    ровно одна сторона."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok

    results: list[object] = []
    barrier = threading.Barrier(2)

    def cancel_worker() -> None:
        barrier.wait()
        results.append(svc.cancel_payment(db, provider, payment_id=outcome.payment_id))

    def finalize_worker() -> None:
        barrier.wait()
        results.append(
            svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
        )

    threads = [threading.Thread(target=cancel_worker), threading.Thread(target=finalize_worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        # Ровно один переход должен был реально произойти — либо SUCCEEDED
        # (finalize выиграл гонку), либо CANCELLED (cancel выиграл, а поздний
        # finalize-вызов из этого же теста тогда — no-op со стороны finalize).
        assert payment.status in (PaymentStatus.SUCCEEDED, PaymentStatus.CANCELLED)
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) in (0, 2)  # либо не выданы, либо выданы РОВНО один раз


def test_finalize_late_success_recovers_tickets_after_cancel(db: Database) -> None:
    """Платёж уже CANCELLED (участник отменил), но банк потом всё же
    подтверждает оплату (напр. сверка выписки пришла с задержкой) — если
    номерки физически ещё свободны, они должны быть выданы автоматически."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
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
    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)
    assert cancel_outcome.applied

    late = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)

    assert late.applied
    assert late.new_status == PaymentStatus.SUCCEEDED
    assert len(late.tickets or []) == 3
    assert not late.late_success_no_tickets
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.SUCCEEDED
        audit_entries = list(
            session.execute(
                select(AuditLog).where(AuditLog.action == "payment_late_success_recovered")
            ).scalars()
        )
        assert len(audit_entries) == 1


def test_finalize_late_success_recovers_ignoring_locked_giveaway(db: Database) -> None:
    """Розыгрыш закрыт/заблокирован администратором ПОСЛЕ отмены, но номерки
    физически ещё свободны — восстановление всё равно должно выдать их,
    т.к. деньги уже списаны (подтверждено владельцем — см. DECISIONS.md)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)
    assert cancel_outcome.applied

    with db.session() as session:
        giveaway = session.get(Giveaway, gid)
        assert giveaway is not None
        giveaway.is_locked = True
        giveaway.is_registration_open = False
        session.flush()

    late = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)

    assert late.applied
    assert len(late.tickets or []) == 2


def test_finalize_late_success_no_tickets_available_writes_audit_and_stays_cancelled(
    db: Database,
) -> None:
    """Розыгрыш почти распродан: после отмены остаток разобрали другие
    покупатели — восстановить номерки не удаётся. Без авто-возврата (ТЗ §21),
    но с чёткой записью в аудит для ручного разбора."""
    gid = make_giveaway(db, max_tickets=2)
    pid = make_participant(db)
    other_pid = make_participant(db, "79990009999")
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)
    assert cancel_outcome.applied
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 2

    other_outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=other_pid,
        participant_phone="79990009999",
        quantity=2,
    )
    assert other_outcome.ok
    other_finalize = svc.finalize_payment(
        db, order_id=other_outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert other_finalize.applied
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 0

    late = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)

    assert not late.applied
    assert late.late_success_no_tickets
    assert not late.tickets
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.CANCELLED  # не переписан
        audit_entries = list(
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "payment_late_success_no_tickets_available"
                )
            ).scalars()
        )
        assert len(audit_entries) == 1
        assert audit_entries[0].entity_id == outcome.payment_id


def test_create_payment_does_not_touch_pool(db: Database) -> None:
    """Провайдер без резервирования "на лету" (requisites_qr) не должен
    трогать пул номеров при создании платежа вообще — деньги по банковскому
    переводу могут идти несколько дней, см. DECISIONS.md."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10  # ничего не зарезервировано
    with db.session() as session:
        giveaway = session.get(Giveaway, gid)
        assert giveaway is not None
        assert giveaway.tickets_reserved == 0


def test_create_payment_assigns_invoice_number(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10, prefix="INV")
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    assert outcome.invoice_no == "INV-00001"
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.payment_number == 1
        giveaway = session.get(Giveaway, gid)
        assert giveaway is not None
        assert giveaway.next_payment_number == 2


def test_payment_number_unique_per_giveaway_not_global(db: Database) -> None:
    """Номер счёта уникален В РАМКАХ розыгрыша (начинается с 1 для каждого),
    а не глобально — см. DECISIONS.md."""
    gid_a = make_giveaway(db, max_tickets=10, prefix="AAA")
    gid_b = make_giveaway(db, max_tickets=10, prefix="BBB")
    pid_a = make_participant(db)
    pid_b = make_participant(db, "79997654321")

    outcome_a = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid_a,
        participant_id=pid_a,
        participant_phone="79991234567",
        quantity=1,
    )
    outcome_b = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid_b,
        participant_id=pid_b,
        participant_phone="79997654321",
        quantity=1,
    )
    assert outcome_a.invoice_no == "AAA-00001"
    assert outcome_b.invoice_no == "BBB-00001"


def test_create_payment_rejects_when_quantity_exceeds_free(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=2)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=5,
    )
    assert not outcome.ok
    assert outcome.free_count == 2
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 0  # платёж не создан вовсе


def test_finalize_success_issues_tickets_now(db: Database) -> None:
    """Без резервирования "на лету" `issue_reserved` в обычной SUCCEEDED-ветке
    ничего не найдёт — должен сработать `_reserve_and_issue_now` (см.
    ARCHITECTURE.md §4)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
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
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10

    result = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)

    assert result.applied
    assert len(result.tickets or []) == 3
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 7
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.SUCCEEDED
        assert payment.oversold is False
        audit_entries = list(
            session.execute(
                select(AuditLog).where(AuditLog.action == "payment_issued_on_confirmation")
            ).scalars()
        )
        assert len(audit_entries) == 1


def test_finalize_success_oversold_when_pool_exhausted(db: Database) -> None:
    """Деньги подтверждены, но к этому моменту пул уже разобран другими
    покупателями (никакого резерва не было, чтобы придержать номерки) —
    Payment.oversold=True, без авто-возврата (ТЗ §21), см. DECISIONS.md."""
    gid = make_giveaway(db, max_tickets=2)
    pid = make_participant(db)
    other_pid = make_participant(db, "79990009999")
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok

    # Другой покупатель успевает оплатить и получить весь остаток, пока первый
    # платёж ещё не подтверждён (резерва под первый платёж нет, значит номерки
    # доступны для кого угодно, кто подтвердит оплату раньше).
    other_outcome = svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=other_pid,
        participant_phone="79990009999",
        quantity=2,
    )
    assert other_outcome.ok
    other_finalize = svc.finalize_payment(
        db, order_id=other_outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert other_finalize.applied
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 0

    result = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)

    assert not result.applied
    assert result.late_success_no_tickets
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.oversold is True
        audit_entries = list(
            session.execute(
                select(AuditLog).where(AuditLog.action == "payment_confirmed_no_tickets_available")
            ).scalars()
        )
        assert len(audit_entries) == 1


def test_cancel_payment_is_noop_on_pool(db: Database) -> None:
    """`cancel()` не вызывает сеть и не должно менять пул (нечего освобождать,
    номерки никогда не резервировались) — статус всё равно переходит в
    CANCELLED."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
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
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10

    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)

    assert cancel_outcome.applied
    assert cancel_outcome.current_status == PaymentStatus.CANCELLED
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


def test_finalize_late_success_after_cancel_still_works(db: Database) -> None:
    """Счёт отменён, но деньги всё же пришли позже (участник оплатил
    статический QR уже после отмены в боте) — восстановление должно
    сработать так же, как после TTL-просрочки."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = make_provider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    cancel_outcome = svc.cancel_payment(db, provider, payment_id=outcome.payment_id)
    assert cancel_outcome.applied

    late = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)

    assert late.applied
    assert len(late.tickets or []) == 2
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 8
