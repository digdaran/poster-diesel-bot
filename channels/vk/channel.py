"""VkChannel — заготовка интерфейса (п.5.4.1, 10.6, 21 ТЗ). Не реализована и не
активна в проде (см. app/channels/factory.ACTIVE_CHANNELS). Целевой вариант по
умолчанию — бот сообщества: `supports_verified_phone=False`,
`can_initiate_dialog=False` (см. п.21 ТЗ); паритет с Telegram по верификации —
только через VK Mini App (роадмап, не входит в объём первой версии).
"""

from __future__ import annotations

from typing import Any

from app.channels.base import BaseMessengerChannel, ChannelCapabilities
from app.models.enums import ChannelType

_NOT_IMPLEMENTED_MSG = (
    "VkChannel — заготовка интерфейса (п.21 ТЗ). Реализация адаптера бота "
    "сообщества ВКонтакте (Bots Longpoll API / Callback API) выполняется отдельной "
    "доработкой, без изменения ядра — см. ARCHITECTURE.md, DECISIONS.md."
)


class VkChannel(BaseMessengerChannel):
    channel_type = ChannelType.VK
    capabilities = ChannelCapabilities(
        supports_verified_phone=False,
        can_initiate_dialog=False,
        supports_inline_buttons=True,
        supports_qr=True,
        media_send_mode="upload",
    )

    async def send_message(self, external_user_id: str, text: str, **kwargs: Any) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def send_media(
        self, external_user_id: str, file_path: str, *, caption: str | None = None
    ) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def request_contact(self, external_user_id: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_keyboard(self, buttons: list[list[str]]) -> Any:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_payment_prompt(self, *, payment_url: str, order_id: str, has_qr: bool) -> Any:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def handle_update(self, update: Any) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
