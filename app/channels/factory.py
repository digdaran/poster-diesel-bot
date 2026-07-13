"""Фабрика каналов-мессенджеров (п.5.4.1, 5.4.3 ТЗ).

В проде на старте активен ТОЛЬКО Telegram (см. ТЗ п.3.3, 5.1). VK/MAX
зарегистрированы как заготовки интерфейса для будущих доработок (п.21 ТЗ),
но фабрика их не инстанцирует без явного запроса — попытка получить
неактивный канал поднимает `ChannelNotActiveError`.
"""

from __future__ import annotations

from app.channels.base import BaseMessengerChannel
from app.models.enums import ChannelType

ACTIVE_CHANNELS: frozenset[ChannelType] = frozenset({ChannelType.TELEGRAM})
"""Каналы, активные в текущей продакшен-версии (п.3.3 ТЗ)."""


class ChannelNotActiveError(Exception):
    pass


def is_channel_active(channel: ChannelType) -> bool:
    return channel in ACTIVE_CHANNELS


def get_channel_class(channel: ChannelType) -> type[BaseMessengerChannel]:
    """Возвращает класс канала по типу. VK/MAX возвращают заготовки (без бизнес-логики,
    методы поднимают NotImplementedError) — они не активны в проде (см. ACTIVE_CHANNELS)."""
    if channel == ChannelType.TELEGRAM:
        from channels.telegram.channel import TelegramChannel

        return TelegramChannel
    if channel == ChannelType.VK:
        from channels.vk.channel import VkChannel

        return VkChannel
    if channel == ChannelType.MAX:
        from channels.max.channel import MaxChannel

        return MaxChannel
    raise ValueError(f"Неизвестный канал: {channel}")
