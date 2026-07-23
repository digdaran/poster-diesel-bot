"""Проактивное уведомление участника об исходе онлайн-платежа — единая точка
для всех вызывающих контекстов вне живого запроса бота (webhook банка,
фоновая сверка — см. backend/webhooks/payments.py, backend/background), по
прямому запросу заказчика (продуктовое решение сверх (локально усечённого)
ТЗ, см. DECISIONS.md).

Раздельно с реактивной доставкой в чат покупателя (channels/*/handlers.py) —
та отвечает в чат, из которого пришёл запрос (важно для подарочных покупок на
неподтверждённый номер без привязки получателя, см. DECISIONS.md), тогда как
это уведомление адресуется участнику-получателю (`Payment.participant_id`)
через его собственную привязку канала и потому не может достучаться до
получателя без привязки — это осознанное ограничение, а не регресс:
реактивный путь остаётся рабочим fallback'ом для этого случая.

Многоканально (Telegram, VK — см. DECISIONS.md #33): выбирается ОДИН канал на
участника, не рассылается на все привязки сразу — см. `_resolve_notify_target`.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select

from app.core.db import Database
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.services import settings_service
from app.services.payment_service import FinalizeOutcome


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


def _resolve_notify_target(db: Database, participant_id: int) -> tuple[ChannelType, str] | None:
    """Выбирает канал и внешний ID для проактивного уведомления получателя.

    Telegram — в приоритете: право писать первым там не отзывается пользователем
    (см. DECISIONS.md #32). VK — только если участник явно разрешил сообщения от
    сообщества (`ChannelBinding.messages_allowed is True`, отзываемо через
    `message_allow`/`message_deny`) — иначе `messages.send` от имени сообщества
    вернёт ошибку запрета отправки. Один канал на участника, не оба сразу — см.
    DECISIONS.md #33."""
    with db.session() as session:
        bindings = list(
            session.execute(
                select(ChannelBinding).where(ChannelBinding.participant_id == participant_id)
            ).scalars()
        )
    by_channel = {b.channel: b for b in bindings}

    telegram_binding = by_channel.get(ChannelType.TELEGRAM)
    if telegram_binding is not None:
        return ChannelType.TELEGRAM, telegram_binding.external_user_id

    vk_binding = by_channel.get(ChannelType.VK)
    if vk_binding is not None and vk_binding.messages_allowed:
        return ChannelType.VK, vk_binding.external_user_id

    return None


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


async def notify_payment_outcome(
    db: Database,
    outcome: FinalizeOutcome,
    *,
    telegram_channel: NotifiableChannel | None,
    vk_channel: NotifiableChannel | None,
) -> None:
    """Отправляет участнику сообщение об исходе платежа: при успехе — постер и
    купленные номерки, при отказе — короткое уведомление. Тихо ничего не
    делает, если у участника нет подходящей привязки канала (напр. подарочная
    покупка на неподтверждённый номер без доступа к аккаунту, п.7.1, 10.3 ТЗ,
    либо процесс поднят без токена нужного канала) — уведомлять там некого."""
    if not outcome.applied or outcome.participant_id is None:
        return

    target = _resolve_notify_target(db, outcome.participant_id)
    if target is None:
        return
    channel_type, external_user_id = target
    channel = _channel_for(channel_type, telegram_channel=telegram_channel, vk_channel=vk_channel)
    if channel is None:
        return

    if outcome.new_status == PaymentStatus.SUCCEEDED:
        with db.session() as session:
            giveaway = (
                session.get(Giveaway, outcome.giveaway_id)
                if outcome.giveaway_id is not None
                else None
            )
        codes = [t.full_code for t in (outcome.tickets or [])]
        await channel.deliver_purchase(
            external_user_id,
            poster_path=giveaway.digital_poster_path if giveaway else None,
            codes=codes,
            intro="Оплата прошла успешно! Ваши номера:",
        )
    else:
        await channel.send_message(external_user_id, _FAILURE_TEXT)


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
    неведении, что деньги списаны."""
    if outcome.participant_id is None:
        return
    target = _resolve_notify_target(db, outcome.participant_id)
    if target is None:
        return
    channel_type, external_user_id = target
    channel = _channel_for(channel_type, telegram_channel=telegram_channel, vk_channel=vk_channel)
    if channel is None:
        return
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
        contacts = platform_settings.support_contacts or {}
    text = _LATE_SUCCESS_NO_TICKETS_TEXT + _format_support_contacts(contacts)
    await channel.send_message(external_user_id, text)
