"""BaseMessengerChannel — единый интерфейс каналов-мессенджеров (п.5.4.1, 10.1 ТЗ).

Вся бизнес-логика (идентификация, покупка, резервирование, выдача, история)
находится в app/services/*, канал отвечает ТОЛЬКО за приём событий платформы
и отрисовку/отправку сообщений её средствами. Реализованы TelegramChannel
(channels/telegram/) и VkChannel (channels/vk/, DECISIONS.md #32/#33) — оба
активны в проде; MaxChannel (channels/max/) — заготовка интерфейса без
бизнес-логики, не регистрируется в проде (см. app/channels/factory.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from app.models.enums import ChannelType

MediaSendMode = Literal["file_id", "upload"]


@dataclass(frozen=True)
class ChannelCapabilities:
    """Флаги возможностей канала (п.5.4.1, 10.6 ТЗ) — логика ветвится по флагам,
    а не по имени канала."""

    supports_verified_phone: bool
    """Есть ли аналог Telegram request_contact с гарантированно принадлежащим номером."""
    can_initiate_dialog: bool
    """Может ли система написать пользователю первой (или только в ответ на его обращение)."""
    supports_inline_buttons: bool
    supports_qr: bool
    media_send_mode: MediaSendMode


class BaseMessengerChannel(ABC):
    channel_type: ChannelType
    capabilities: ChannelCapabilities

    @abstractmethod
    async def send_message(self, external_user_id: str, text: str, **kwargs: Any) -> None:
        """Отправляет текстовое сообщение пользователю канала."""

    @abstractmethod
    async def send_media(
        self, external_user_id: str, file_path: str, *, caption: str | None = None
    ) -> None:
        """Отправляет медиа (цифровой постер) из файла — канал сам решает, как
        загрузить/закэшировать media-id (`Giveaway.poster_media_cache`, п.6.2 ТЗ)."""

    @abstractmethod
    async def request_contact(self, external_user_id: str) -> None:
        """Запрашивает подтверждённый номер телефона (если canal поддерживает
        `supports_verified_phone`), иначе должен явно сообщить о невозможности."""

    @abstractmethod
    def render_keyboard(self, buttons: list[list[str]]) -> Any:
        """UI-примитив: обычная (reply) клавиатура из подписей кнопок."""

    @abstractmethod
    def render_payment_prompt(self, *, payment_url: str, order_id: str, has_qr: bool) -> Any:
        """UI-примитив: приглашение к оплате (ссылка, кнопка «Показать QR» если
        `has_qr`, кнопка проверки статуса)."""

    @abstractmethod
    async def handle_update(self, update: Any) -> None:
        """Приём входящего события платформы (используется при webhook-режиме;
        для long polling диспетчеризация может идти напрямую через SDK канала)."""
