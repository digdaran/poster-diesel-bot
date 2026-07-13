"""Общие зависимости Telegram-канала: БД, настройки, активный платёжный провайдер,
FSM-состояния диалога покупки (п.8.1, 10.2 ТЗ)."""

from __future__ import annotations

from functools import lru_cache

from aiogram.fsm.state import State, StatesGroup
from app.core.config import get_settings
from app.core.db import Database
from app.payments.base import BasePaymentProvider
from app.payments.factory import get_provider
from app.services import settings_service


@lru_cache
def get_channel_db() -> Database:
    return Database(get_settings())


def get_active_provider(db: Database) -> BasePaymentProvider:
    """Провайдер с учётом приоритета PlatformSettings.payment_provider_override
    над .env (п.9.3 ТЗ)."""
    settings = get_settings()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
        override = platform_settings.payment_provider_override
    return get_provider(settings, override=override)


class PurchaseStates(StatesGroup):
    choosing_giveaway = State()
    choosing_quantity = State()
    awaiting_phone_for_gift = State()
