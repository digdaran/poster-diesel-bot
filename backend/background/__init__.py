"""Фоновая сверка платежей и освобождение просроченных резервов (п.7.5, 9 ТЗ,
ARCHITECTURE.md — "фоновые задачи" процесса backend).

Единственное место, где реально запускаются `payment_service.poll_pending_payment`
и обработка `ticket_pool_repo.find_expired_reservation_refs` — раньше эти функции
существовали и были покрыты тестами, но нигде не вызывались, из-за чего платежи
оставались в PENDING бессрочно."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import TYPE_CHECKING

import structlog
from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from app.payments.factory import create_provider
from app.repositories import ticket_pool_repo as pool_repo
from app.services import manual_registration_service as manual_svc
from app.services import notification_service
from app.services import payment_service as payment_svc
from app.services.payment_service import FinalizeOutcome
from sqlalchemy import select

if TYPE_CHECKING:
    from channels.telegram.channel import TelegramChannel
    from channels.vk.channel import VkChannel

logger = structlog.get_logger(__name__)


def _reconcile_pending_payments(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> list[FinalizeOutcome]:
    with db.session() as session:
        pending: list[tuple[int, PaymentProviderType]] = list(
            session.execute(
                select(Payment.id, Payment.provider).where(Payment.status == PaymentStatus.PENDING)
            )
            .tuples()
            .all()
        )
    if not pending:
        return []

    outcomes: list[FinalizeOutcome] = []
    for payment_id, provider_type in pending:
        try:
            # Сверяем статус ЧЕРЕЗ ТОТ БАНК, которым конкретный платёж был создан
            # (`Payment.provider`), а не через единый "текущий активный" провайдер —
            # активный провайдер мог смениться в панели между созданием этого
            # платежа и текущим тиком сверки (п.9.3 ТЗ, DECISIONS.md).
            provider = create_provider(settings, provider_type)
            result = payment_svc.poll_pending_payment(
                db,
                provider,
                payment_id=payment_id,
                max_attempts=settings.online_status_poll_max_attempts,
                ttl_seconds=settings.online_reservation_ttl_sec,
                now=now,
            )
            if result is not None and result.outcome is not None:
                outcome = result.outcome
                if outcome.applied or outcome.late_success_no_tickets:
                    outcomes.append(outcome)
        except Exception:
            logger.exception("payment_poll_failed", payment_id=payment_id)
    return outcomes


def _release_expired_manual_registrations(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> None:
    now = now or utcnow()
    with db.session() as session:
        refs = pool_repo.find_expired_reservation_refs(session, now=now)
    manual_ids = [ref_id for kind, ref_id in refs if kind == "manual"]

    for manual_registration_id in manual_ids:
        try:
            manual_svc.cancel_manual_registration(
                db, manual_registration_id=manual_registration_id, now=now
            )
        except manual_svc.ManualRegistrationStateError:
            # Гонка: оператор успел подтвердить/отменить между чтением списка и этим вызовом.
            pass
        except Exception:
            logger.exception(
                "manual_registration_ttl_release_failed",
                manual_registration_id=manual_registration_id,
            )


async def run_background_loop(
    db: Database,
    settings: Settings,
    telegram_channel: TelegramChannel | None = None,
    vk_channel: VkChannel | None = None,
) -> None:
    """Работает, пока не отменена (см. backend/main.py::lifespan). Синхронная
    работа (SQLite-сессии, сетевые вызовы check_status у банков) выполняется в
    отдельном потоке через `asyncio.to_thread`, чтобы не блокировать event loop
    и не задерживать обработку конкурентных HTTP-запросов. Уведомления о
    финализированных платежах (`notification_service`) отправляются уже после
    возврата на event loop, т.к. отправка сообщений в мессенджер — асинхронный
    сетевой вызов. `notification_service` сам выбирает канал получателя
    (Telegram/VK) по привязкам — см. DECISIONS.md #33."""
    while True:
        try:
            outcomes = await asyncio.to_thread(_reconcile_pending_payments, db, settings)
            await asyncio.to_thread(_release_expired_manual_registrations, db, settings)
            if telegram_channel is not None or vk_channel is not None:
                for outcome in outcomes:
                    if outcome.late_success_no_tickets:
                        await notification_service.notify_late_success_no_tickets(
                            db, outcome, telegram_channel=telegram_channel, vk_channel=vk_channel
                        )
                    else:
                        await notification_service.notify_payment_outcome(
                            db, outcome, telegram_channel=telegram_channel, vk_channel=vk_channel
                        )
        except Exception:
            logger.exception("background_reconciliation_tick_failed")
        await asyncio.sleep(settings.online_status_poll_interval_sec)
