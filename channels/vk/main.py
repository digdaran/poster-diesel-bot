"""Точка входа процесса `channel-vk` (п.5.1, 10.6, 19 ТЗ): vkbottle, VK Bots
Long Poll API, отдельный процесс/образ со своими зависимостями — см.
ARCHITECTURE.md §7.1, DECISIONS_LOG.md #32."""

from __future__ import annotations

import asyncio

import structlog
from app.core.config import get_settings

from channels.vk.channel import VkChannel
from channels.vk.dispatcher import setup_dispatcher
from channels.vk.handlers import set_channel

logger = structlog.get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    if not settings.vk_group_token:
        raise RuntimeError("VK_GROUP_TOKEN не задан — канал VK не может быть запущен")

    channel = VkChannel(token=settings.vk_group_token, group_id=settings.vk_group_id)
    set_channel(channel)
    setup_dispatcher(channel.bot)

    logger.info("vk_channel_starting")
    await channel.bot.run_polling()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
