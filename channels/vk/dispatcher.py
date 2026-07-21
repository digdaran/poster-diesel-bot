"""Подключение обработчиков VK-канала (`channels/vk/handlers.py`) к `Bot` —
по структуре аналог `channels/telegram/dispatcher.py`. В отличие от aiogram,
vkbottle не разделяет клиента и диспетчер на два объекта — у `Bot` уже есть
собственный `labeler`, поэтому здесь только загружается набор обработчиков."""

from __future__ import annotations

from vkbottle.bot import Bot

from channels.vk.handlers import labeler


def setup_dispatcher(bot: Bot) -> None:
    bot.labeler.load(labeler)
