"""Фабрика каналов-мессенджеров (п.5.4.1, 5.4.3 ТЗ).

В проде активны Telegram и VK (см. ТЗ п.3.3, 5.1; включение VK — по прямому
запросу заказчика, см. DECISIONS_LOG.md #33). MAX остаётся заготовкой интерфейса
для будущей доработки (п.21 ТЗ) — фабрика её не инстанцирует без явного
запуска; попытка получить неактивный канал поднимает `ChannelNotActiveError`.
"""

from __future__ import annotations

from app.channels.base import BaseMessengerChannel
from app.models.enums import ChannelType

ACTIVE_CHANNELS: frozenset[ChannelType] = frozenset({ChannelType.TELEGRAM, ChannelType.VK})
"""Каналы, активные в текущей продакшен-версии (п.3.3 ТЗ, DECISIONS_LOG.md #33)."""


class ChannelNotActiveError(Exception):
    pass


def is_channel_active(channel: ChannelType) -> bool:
    return channel in ACTIVE_CHANNELS


def get_channel_class(channel: ChannelType) -> type[BaseMessengerChannel]:
    """Возвращает класс канала по типу. MAX — заготовка (без бизнес-логики,
    методы поднимают NotImplementedError), не активна в проде (см. ACTIVE_CHANNELS)."""
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
