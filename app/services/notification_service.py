"""Проактивное уведомление участника об исходе онлайн-платежа — единая точка
для всех вызывающих контекстов вне живого запроса бота (webhook банка,
фоновая сверка — см. backend/webhooks/payments.py, backend/background), по
прямому запросу заказчика (продуктовое решение сверх (локально усечённого)
ТЗ, см. DECISIONS.md).

Раздельно с реактивной доставкой в чат покупателя
(channels/telegram/handlers.py::_deliver_tickets) — та отвечает в чат, из
которого пришёл запрос (важно для подарочных покупок на неподтверждённый
номер без привязки получателя, см. DECISIONS.md), тогда как это уведомление
адресуется участнику-получателю (`Payment.participant_id`) через его
собственную привязку канала и потому не может достучаться до получателя без
привязки — это осознанное ограничение, а не регресс: реактивный путь остаётся
рабочим fallback'ом для этого случая.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.db import Database
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.services.payment_service import FinalizeOutcome

if TYPE_CHECKING:
    from channels.telegram.channel import TelegramChannel

_FAILURE_TEXT = "Платёж не прошёл. Резерв снят — можете попробовать оформить покупку заново."
_LATE_SUCCESS_NO_TICKETS_TEXT = (
    "Ваш платёж всё же прошёл успешно, но, к сожалению, к этому моменту свободные "
    "экземпляры закончились. Пожалуйста, обратитесь в поддержку — деньги не потеряны, "
    "мы решим вопрос вручную."
)


def _resolve_telegram_external_id(db: Database, participant_id: int) -> str | None:
    with db.session() as session:
        binding = (
            session.execute(
                select(ChannelBinding).where(
                    ChannelBinding.participant_id == participant_id,
                    ChannelBinding.channel == ChannelType.TELEGRAM,
                )
            )
            .scalars()
            .first()
        )
        return binding.external_user_id if binding else None


async def notify_payment_outcome(
    db: Database, telegram_channel: TelegramChannel, outcome: FinalizeOutcome
) -> None:
    """Отправляет участнику сообщение об исходе платежа: при успехе — постер и
    купленные номерки, при отказе — короткое уведомление. Тихо ничего не
    делает, если у участника нет привязки Telegram (напр. подарочная покупка
    на неподтверждённый номер без доступа к аккаунту, п.7.1, 10.3 ТЗ) —
    уведомлять там некого."""
    if not outcome.applied or outcome.participant_id is None:
        return

    external_user_id = _resolve_telegram_external_id(db, outcome.participant_id)
    if external_user_id is None:
        return

    if outcome.new_status == PaymentStatus.SUCCEEDED:
        with db.session() as session:
            giveaway = (
                session.get(Giveaway, outcome.giveaway_id)
                if outcome.giveaway_id is not None
                else None
            )
        codes = [t.full_code for t in (outcome.tickets or [])]
        await telegram_channel.deliver_purchase(
            external_user_id,
            poster_path=giveaway.digital_poster_path if giveaway else None,
            codes=codes,
            intro="Оплата прошла успешно! Ваши номера:",
        )
    else:
        await telegram_channel.send_message(external_user_id, _FAILURE_TEXT)


async def notify_late_success_no_tickets(
    db: Database, telegram_channel: TelegramChannel, outcome: FinalizeOutcome
) -> None:
    """Платёж подтверждён банком уже ПОСЛЕ того, как он был помечен
    CANCELLED/FAILED (отмена участником или истечение TTL) и резерв роздан —
    повторно захватить номерки не удалось (см.
    `payment_service._recover_late_success`, DECISIONS.md). Автоматический
    возврат не делается (ТЗ §21) — сообщаем участнику, чтобы он не остался в
    неведении, что деньги списаны."""
    if outcome.participant_id is None:
        return
    external_user_id = _resolve_telegram_external_id(db, outcome.participant_id)
    if external_user_id is None:
        return
    await telegram_channel.send_message(external_user_id, _LATE_SUCCESS_NO_TICKETS_TEXT)
