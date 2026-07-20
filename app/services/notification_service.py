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

    with db.session() as session:
        binding = (
            session.execute(
                select(ChannelBinding).where(
                    ChannelBinding.participant_id == outcome.participant_id,
                    ChannelBinding.channel == ChannelType.TELEGRAM,
                )
            )
            .scalars()
            .first()
        )
        if binding is None:
            return
        external_user_id = binding.external_user_id
        giveaway = (
            session.get(Giveaway, outcome.giveaway_id) if outcome.giveaway_id is not None else None
        )

    if outcome.new_status == PaymentStatus.SUCCEEDED:
        codes = [t.full_code for t in (outcome.tickets or [])]
        await telegram_channel.deliver_purchase(
            external_user_id,
            poster_path=giveaway.digital_poster_path if giveaway else None,
            codes=codes,
            intro="Оплата прошла успешно! Ваши номерки:",
        )
    else:
        await telegram_channel.send_message(external_user_id, _FAILURE_TEXT)
