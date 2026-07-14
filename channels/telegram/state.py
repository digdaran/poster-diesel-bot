"""Общие зависимости Telegram-канала: БД, настройки, активный платёжный провайдер,
FSM-состояния диалога покупки (п.8.1, 10.2 ТЗ)."""

from __future__ import annotations

from functools import lru_cache

from aiogram.fsm.state import State, StatesGroup
from app.core.config import get_settings
from app.core.db import Database
from app.payments import factory as payment_factory
from app.payments.base import BasePaymentProvider


@lru_cache
def get_channel_db() -> Database:
    return Database(get_settings())


def get_active_provider(db: Database) -> BasePaymentProvider:
    """Провайдер с учётом приоритета PlatformSettings.payment_provider_override
    над .env (п.9.3 ТЗ) — тонкая обёртка над app.payments.factory (единая точка
    резолва для каналов и фоновых задач backend, см. DECISIONS.md)."""
    return payment_factory.get_active_provider(db, get_settings())


QUANTITY_OPTIONS: tuple[int, ...] = (1, 3, 5, 10)


class PurchaseStates(StatesGroup):
    choosing_giveaway = State()
    choosing_quantity = State()
    awaiting_phone_for_gift = State()


class RegistrationStates(StatesGroup):
    awaiting_name = State()
