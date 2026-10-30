"""Акция «за каждые N купленных за раз — M в подарок» (DECISIONS_LOG.md №87).

Подарки выдаются в момент подтверждения покупки (оплата / «Подтвердить» у ручной
регистрации), сразу после выдачи платных номеров и в той же `BEGIN IMMEDIATE`-транзакции.
Пул и счётчики коллекции — общие с платными номерами (лимит тиража учитывает и те, и другие).
Если подарков не хватает, выдаётся сколько влезло, остаток записывается в `gifts_not_issued`
покупки для ручной выдачи в следующем тираже. Продажу это не отменяет.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.enums import AuditActorType, TicketSource
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.repositories import ticket_pool_repo as repo
from app.services import audit_service


@dataclass(frozen=True)
class GiftOutcome:
    tickets: list[Ticket] = field(default_factory=list)
    not_issued: int = 0


def issue_gifts(
    session: Session,
    *,
    giveaway: Giveaway,
    owner: Payment | ManualRegistration,
    now: dt.datetime,
) -> GiftOutcome:
    """Резервирует и сразу выдаёт подарки по `owner.gift_quantity`. Вызывать только после
    того, как платные номера покупки уже выданы: здесь `issue_reserved` по владельцу
    подхватывает именно подарочный резерв."""
    wanted = owner.gift_quantity
    if wanted <= 0:
        return GiftOutcome()

    is_payment = isinstance(owner, Payment)
    owner_ref: dict[str, int] = (
        {"payment_id": owner.id} if is_payment else {"manual_registration_id": owner.id}
    )
    repo.reserve_up_to(
        session,
        giveaway_id=giveaway.id,
        quantity=wanted,
        participant_id=owner.participant_id,
        reserved_until=now,
        **owner_ref,
    )
    issued_rows = repo.issue_reserved(session, issued_at=now, **owner_ref)
    tickets = [
        Ticket(
            giveaway_id=giveaway.id,
            pool_id=row.id,
            number=row.number,
            full_code=giveaway.format_code(row.number),
            participant_id=owner.participant_id,
            source=TicketSource.ONLINE if is_payment else TicketSource.MANUAL,
            is_gift=True,
            **owner_ref,
        )
        for row in issued_rows
    ]
    session.add_all(tickets)
    not_issued = wanted - len(tickets)
    if not_issued:
        mark_gifts_not_issued(session, owner=owner, count=not_issued)
    session.flush()
    _audit(session, owner=owner, issued=len(tickets), not_issued=not_issued)
    return GiftOutcome(tickets=tickets, not_issued=not_issued)


def mark_gifts_not_issued(
    session: Session, *, owner: Payment | ManualRegistration, count: int
) -> None:
    """Записывает, сколько подарков покупки придётся выдать вручную."""
    model = Payment if isinstance(owner, Payment) else ManualRegistration
    session.execute(update(model).where(model.id == owner.id).values(gifts_not_issued=count))


def _audit(
    session: Session, *, owner: Payment | ManualRegistration, issued: int, not_issued: int
) -> None:
    audit_service.log(
        session,
        action="promo_gifts_not_issued" if not_issued else "promo_gifts_issued",
        actor_type=AuditActorType.SYSTEM,
        actor_label="promo_service",
        entity_type="payment" if isinstance(owner, Payment) else "manual_registration",
        entity_id=owner.id,
        details={
            "giveaway_id": owner.giveaway_id,
            "gift_quantity": owner.gift_quantity,
            "issued": issued,
            "not_issued": not_issued,
        },
    )
