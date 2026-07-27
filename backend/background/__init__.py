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
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from app.payments.factory import create_provider
from app.repositories import ticket_pool_repo as pool_repo
from app.services import bank_reconciliation_service, notification_service
from app.services import manual_registration_service as manual_svc
from app.services import payment_service as payment_svc
from app.services.payment_service import FinalizeOutcome
from sqlalchemy import select, update

if TYPE_CHECKING:
    from channels.telegram.channel import TelegramChannel
    from channels.vk.channel import VkChannel

logger = structlog.get_logger(__name__)


def _reconcile_pending_payments(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> list[FinalizeOutcome]:
    """Резервная сверка через `check_status` — только для провайдеров с
    резервированием "на лету" (Т-Банк/ВТБ/mock, `reserves_tickets_on_create=True`).
    `requisites_qr` не резервирует номерки и подтверждается батч-сверкой выписки
    (`bank_reconciliation_service.reconcile`, отдельный шаг фонового цикла ниже) —
    смешивать эти два платежа было бы неверно: TTL здесь короткий (секунды/минуты,
    `ONLINE_RESERVATION_TTL_SEC`), а деньги по банковскому переводу могут идти
    несколько дней (см. DECISIONS.md)."""
    with db.session() as session:
        pending: list[tuple[int, PaymentProviderType]] = list(
            session.execute(
                select(Payment.id, Payment.provider).where(
                    Payment.status == PaymentStatus.PENDING,
                    Payment.provider != PaymentProviderType.REQUISITES_QR,
                )
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


_VK_PERMISSION_RECONCILE_BATCH = 50


async def _reconcile_vk_permissions(db: Database, vk_channel: VkChannel | None) -> None:
    """Сверяет `ChannelBinding.messages_allowed` с реальным состоянием в VK
    (`VkChannel.is_messages_allowed`) для привязок, где локальный флаг ещё не
    известен (`IS NULL`) — см. DECISIONS_LOG.md №61. Локальный флаг обновляется
    только пассивно по Long Poll `message_allow`/`message_deny`
    (`channels/vk/handlers.py`), а VK не всегда шлёт `message_allow`, если
    пользователь сам начал переписку с сообществом первым — разрешение у VK уже
    действует, но наш кэш так и остаётся `NULL` навсегда без этой сверки.
    Батч ограничен, чтобы не заливать VK API запросами при росте базы —
    непросверенные строки просто дождутся следующего тика."""
    if vk_channel is None:
        return
    with db.session() as session:
        bindings = list(
            session.execute(
                select(ChannelBinding.id, ChannelBinding.external_user_id)
                .where(
                    ChannelBinding.channel == ChannelType.VK,
                    ChannelBinding.messages_allowed.is_(None),
                )
                .limit(_VK_PERMISSION_RECONCILE_BATCH)
            )
            .tuples()
            .all()
        )
    for binding_id, external_user_id in bindings:
        try:
            allowed = await vk_channel.is_messages_allowed(external_user_id)
        except Exception:
            logger.exception("vk_messages_allowed_check_failed", external_user_id=external_user_id)
            continue
        with db.session() as session:
            session.execute(
                update(ChannelBinding)
                .where(ChannelBinding.id == binding_id)
                .values(messages_allowed=allowed)
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
    сетевой вызов. `notification_service` сам выбирает каналы получателя
    (Telegram И VK одновременно, по привязкам) — см. DECISIONS_LOG.md №43.
    Дополнительно каждый тик сверяет `ChannelBinding.messages_allowed` для VK
    через `_reconcile_vk_permissions` — см. DECISIONS_LOG.md №61."""
    while True:
        try:
            outcomes = await asyncio.to_thread(_reconcile_pending_payments, db, settings)
            outcomes += await asyncio.to_thread(bank_reconciliation_service.reconcile, db, settings)
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
            await _reconcile_vk_permissions(db, vk_channel)
        except Exception:
            logger.exception("background_reconciliation_tick_failed")
        await asyncio.sleep(settings.online_status_poll_interval_sec)
