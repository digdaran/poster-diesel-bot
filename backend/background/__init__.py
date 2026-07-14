"""Фоновая сверка платежей и освобождение просроченных резервов (п.7.5, 9 ТЗ,
ARCHITECTURE.md — "фоновые задачи" процесса backend).

Единственное место, где реально запускаются `payment_service.poll_pending_payment`
и обработка `ticket_pool_repo.find_expired_reservation_refs` — раньше эти функции
существовали и были покрыты тестами, но нигде не вызывались, из-за чего платежи
оставались в PENDING бессрочно."""

from __future__ import annotations

import asyncio
import datetime as dt

import structlog
from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PaymentStatus
from app.models.payment import Payment
from app.payments.factory import get_active_provider
from app.repositories import ticket_pool_repo as pool_repo
from app.services import manual_registration_service as manual_svc
from app.services import payment_service as payment_svc
from sqlalchemy import select

logger = structlog.get_logger(__name__)


def _reconcile_pending_payments(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> None:
    with db.session() as session:
        pending_ids = [
            pid
            for (pid,) in session.execute(
                select(Payment.id).where(Payment.status == PaymentStatus.PENDING)
            ).all()
        ]
    if not pending_ids:
        return

    provider = get_active_provider(db, settings)
    for payment_id in pending_ids:
        try:
            payment_svc.poll_pending_payment(
                db,
                provider,
                payment_id=payment_id,
                max_attempts=settings.online_status_poll_max_attempts,
                ttl_seconds=settings.online_reservation_ttl_sec,
                now=now,
            )
        except Exception:
            logger.exception("payment_poll_failed", payment_id=payment_id)


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


async def run_background_loop(db: Database, settings: Settings) -> None:
    """Работает, пока не отменена (см. backend/main.py::lifespan). Синхронная
    работа (SQLite-сессии, сетевые вызовы check_status у банков) выполняется в
    отдельном потоке через `asyncio.to_thread`, чтобы не блокировать event loop
    и не задерживать обработку конкурентных HTTP-запросов."""
    while True:
        try:
            await asyncio.to_thread(_reconcile_pending_payments, db, settings)
            await asyncio.to_thread(_release_expired_manual_registrations, db, settings)
        except Exception:
            logger.exception("background_reconciliation_tick_failed")
        await asyncio.sleep(settings.online_status_poll_interval_sec)
