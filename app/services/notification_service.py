"""Проактивное уведомление участника об исходе онлайн-платежа и о подтверждении
ручной (офлайн) регистрации — единая точка для всех вызывающих контекстов вне
живого запроса бота (фоновая сверка банковской выписки — см.
app/services/bank_reconciliation_service.py, backend/background; подтверждение
ручной регистрации оператором — см. backend/api/manual_registrations.py), по
прямому запросу заказчика (продуктовое решение сверх (локально усечённого) ТЗ,
см. DECISIONS.md).

Адресуется участнику-получателю (`Payment.participant_id`) через ВСЕ его
привязки каналов одновременно (`_resolve_notify_targets`). Если у получателя
нет ни одной привязки (подарочная покупка на неподтверждённый номер получателя,
см. `resolve_manual_recipient`/DECISIONS.md) — доставка идёт в чат, ГДЕ БЫЛА
ИНИЦИИРОВАНА покупка, через `Payment.channel`/`initiating_external_user_id`
(см. `_resolve_targets_with_fallback`, DECISIONS_LOG.md №56) — раньше для этого
случая была отдельная реактивная кнопка «Проверить статус оплаты», которая уже
не работала на практике (покупатель тоже без привязки, см. DECISIONS_LOG.md
№55) и была убрана; этот fallback — её замена.

Многоканально (Telegram, VK): по прямому запросу заказчика уведомление уходит
**во все** каналы, где есть подходящая привязка, одновременно — см.
`_resolve_notify_targets` (DECISIONS_LOG.md №43, отменяет "один канал" из №33).
"""

from __future__ import annotations

import random
from typing import Any, Protocol

import structlog
from sqlalchemy import select

from app.core.db import Database
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.ticket import Ticket
from app.services import settings_service
from app.services.payment_service import FinalizeOutcome

logger = structlog.get_logger(__name__)


class NotifiableChannel(Protocol):
    """Структурный протокол вместо конкретного класса канала: `TelegramChannel`
    и `VkChannel` не имеют общего базового класса для `deliver_purchase`/
    `send_message` (не входят в `BaseMessengerChannel`, см. app/channels/base.py),
    но обе реализации совпадают по сигнатуре — этого достаточно."""

    async def send_message(self, external_user_id: str, text: str, **kwargs: Any) -> None: ...

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None: ...


_FAILURE_TEXT = "Платёж не прошёл. Можете попробовать оформить покупку заново."
_LATE_SUCCESS_NO_TICKETS_TEXT = (
    "Ваш платёж всё же прошёл успешно, но, к сожалению, к этому моменту свободные "
    "экземпляры закончились. Пожалуйста, обратитесь в поддержку для возврата средств — "
    "деньги не потеряны, мы решим вопрос вручную."
)


def _format_support_contacts(contacts: dict[str, Any]) -> str:
    """Та же приписка контактов поддержки, что уже показывается в `on_help`
    (`channels/*/handlers.py`) — переиспользуется здесь для сообщения об
    отсутствии свободных номерков после подтверждённой оплаты."""
    if not contacts:
        return ""
    lines = ["\nПоддержка:"]
    lines.extend(f"{key}: {value}" for key, value in contacts.items())
    return "\n".join(lines)


def _resolve_notify_targets(db: Database, participant_id: int) -> list[tuple[ChannelType, str]]:
    """Выбирает ВСЕ каналы, куда нужно доставить проактивное уведомление
    получателя — по прямому запросу заказчика уведомление уходит в Telegram
    И VK одновременно, если у участника есть обе подходящие привязки
    (DECISIONS_LOG.md №43, отменяет "один канал" из №33).

    VK участвует только если участник явно разрешил сообщения от сообщества
    (`ChannelBinding.messages_allowed is True`, отзываемо через
    `message_allow`/`message_deny`, см. DECISIONS_LOG.md #32) — иначе
    `messages.send` от имени сообщества вернёт ошибку запрета отправки."""
    with db.session() as session:
        bindings = list(
            session.execute(
                select(ChannelBinding).where(ChannelBinding.participant_id == participant_id)
            ).scalars()
        )
    by_channel = {b.channel: b for b in bindings}

    targets: list[tuple[ChannelType, str]] = []
    telegram_binding = by_channel.get(ChannelType.TELEGRAM)
    if telegram_binding is not None:
        targets.append((ChannelType.TELEGRAM, telegram_binding.external_user_id))

    vk_binding = by_channel.get(ChannelType.VK)
    if vk_binding is not None and vk_binding.messages_allowed:
        targets.append((ChannelType.VK, vk_binding.external_user_id))

    return targets


def _channel_for(
    channel_type: ChannelType,
    *,
    telegram_channel: NotifiableChannel | None,
    vk_channel: NotifiableChannel | None,
) -> NotifiableChannel | None:
    if channel_type == ChannelType.TELEGRAM:
        return telegram_channel
    if channel_type == ChannelType.VK:
        return vk_channel
    return None


def _resolve_targets_with_fallback(
    db: Database, outcome: FinalizeOutcome
) -> list[tuple[ChannelType, str]]:
    """`_resolve_notify_targets` по `ChannelBinding` получателя, а если пусто (нет
    привязки — типично подарочная покупка на неподтверждённый номер получателя,
    см. `resolve_manual_recipient`) — fallback на чат, ГДЕ БЫЛА ИНИЦИИРОВАНА
    покупка (`Payment.channel`/`initiating_external_user_id`, см.
    DECISIONS_LOG.md №55/№56). Без этого fallback'а такие покупки не имели ни
    одного рабочего способа доставки: ни получатель, ни сам покупатель не
    привязаны, `ChannelBinding` для уведомления искать не у кого."""
    assert outcome.participant_id is not None
    targets = _resolve_notify_targets(db, outcome.participant_id)
    if targets:
        return targets
    if outcome.initiating_channel is not None and outcome.initiating_external_user_id is not None:
        return [(outcome.initiating_channel, outcome.initiating_external_user_id)]
    return []


async def notify_payment_outcome(
    db: Database,
    outcome: FinalizeOutcome,
    *,
    telegram_channel: NotifiableChannel | None,
    vk_channel: NotifiableChannel | None,
) -> None:
    """Отправляет участнику сообщение об исходе платежа: при успехе — постер и
    купленные номерки, при отказе — короткое уведомление. Уходит во ВСЕ каналы
    с подходящей привязкой одновременно (см. `_resolve_notify_targets`), а если
    подходящих привязок нет — в чат, откуда была инициирована покупка (см.
    `_resolve_targets_with_fallback`). Тихо ничего не делает, только если ни
    привязки, ни инициирующего чата нет вовсе (платёж создан не через бота —
    напр. панель, либо процесс поднят без токена нужного канала)."""
    if not outcome.applied or outcome.participant_id is None:
        return

    targets = _resolve_targets_with_fallback(db, outcome)
    if not targets:
        return

    poster_path: str | None = None
    if outcome.new_status == PaymentStatus.SUCCEEDED and outcome.giveaway_id is not None:
        with db.session() as session:
            giveaway = session.get(Giveaway, outcome.giveaway_id)
            if giveaway is not None and giveaway.posters:
                poster_path = random.choice([p.file_path for p in giveaway.posters])
    codes = [t.full_code for t in (outcome.tickets or [])]

    for channel_type, external_user_id in targets:
        channel = _channel_for(
            channel_type, telegram_channel=telegram_channel, vk_channel=vk_channel
        )
        if channel is None:
            continue
        try:
            if outcome.new_status == PaymentStatus.SUCCEEDED:
                await channel.deliver_purchase(
                    external_user_id,
                    poster_path=poster_path,
                    codes=codes,
                    intro="Оплата прошла успешно! Ваши постеры куплены, номера:",
                )
            else:
                await channel.send_message(external_user_id, _FAILURE_TEXT)
        except Exception:
            # Один канал не должен блокировать доставку в другой — напр. VK
            # может вернуть ошибку запрета отправки, если участник отозвал
            # разрешение прямо между сверкой привязок и отправкой.
            logger.exception(
                "notify_payment_outcome_channel_failed",
                channel=channel_type.value,
                participant_id=outcome.participant_id,
            )


async def notify_late_success_no_tickets(
    db: Database,
    outcome: FinalizeOutcome,
    *,
    telegram_channel: NotifiableChannel | None,
    vk_channel: NotifiableChannel | None,
) -> None:
    """Платёж подтверждён банком уже ПОСЛЕ того, как он был помечен
    CANCELLED/FAILED (отмена участником или истечение TTL) и резерв роздан —
    повторно захватить номерки не удалось (см.
    `payment_service._recover_late_success`, DECISIONS.md). Автоматический
    возврат не делается (ТЗ §21) — сообщаем участнику, чтобы он не остался в
    неведении, что деньги списаны. Уходит во ВСЕ каналы с подходящей
    привязкой одновременно, либо в чат инициатора покупки, если подходящей
    привязки нет (см. `_resolve_targets_with_fallback`)."""
    if outcome.participant_id is None:
        return
    targets = _resolve_targets_with_fallback(db, outcome)
    if not targets:
        return
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
        contacts = platform_settings.support_contacts or {}
    text = _LATE_SUCCESS_NO_TICKETS_TEXT + _format_support_contacts(contacts)

    for channel_type, external_user_id in targets:
        channel = _channel_for(
            channel_type, telegram_channel=telegram_channel, vk_channel=vk_channel
        )
        if channel is None:
            continue
        try:
            await channel.send_message(external_user_id, text)
        except Exception:
            logger.exception(
                "notify_late_success_no_tickets_channel_failed",
                channel=channel_type.value,
                participant_id=outcome.participant_id,
            )


async def notify_manual_registration_confirmed(
    db: Database,
    *,
    participant_id: int,
    giveaway_id: int,
    tickets: list[Ticket],
    telegram_channel: NotifiableChannel | None,
    vk_channel: NotifiableChannel | None,
) -> None:
    """Уведомляет участника о выдаче номерков по ручной (офлайн) регистрации,
    подтверждённой оператором в панели (п.7.7 ТЗ). Уходит во ВСЕ каналы с
    подходящей привязкой одновременно, как и `notify_payment_outcome` (см.
    `_resolve_notify_targets`, DECISIONS_LOG.md №43) — в отличие от онлайн-оплаты
    здесь нет чата-инициатора покупки (регистрацию создаёт оператор в панели,
    не участник в боте), поэтому фолбэка на инициирующий чат нет: если у
    участника нет ни одной привязки каналов — тихо ничего не делает."""
    targets = _resolve_notify_targets(db, participant_id)
    if not targets:
        return

    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
        poster_path = (
            random.choice([p.file_path for p in giveaway.posters])
            if giveaway is not None and giveaway.posters
            else None
        )
    codes = [t.full_code for t in tickets]

    for channel_type, external_user_id in targets:
        channel = _channel_for(
            channel_type, telegram_channel=telegram_channel, vk_channel=vk_channel
        )
        if channel is None:
            continue
        try:
            await channel.deliver_purchase(
                external_user_id,
                poster_path=poster_path,
                codes=codes,
                intro="Ваша регистрация подтверждена! Ваши постеры куплены, номера:",
            )
        except Exception:
            logger.exception(
                "notify_manual_registration_confirmed_channel_failed",
                channel=channel_type.value,
                participant_id=participant_id,
            )
