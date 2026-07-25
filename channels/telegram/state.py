"""Общие зависимости Telegram-канала: БД, настройки, активный платёжный провайдер,
FSM-состояния диалога покупки (п.8.1, 10.2 ТЗ)."""

from __future__ import annotations

from functools import lru_cache

from aiogram.fsm.state import State, StatesGroup
from app.core.config import get_settings
from app.core.db import Database
from app.models.enums import PaymentProviderType
from app.payments import factory as payment_factory
from app.payments.base import BasePaymentProvider


@lru_cache
def get_channel_db() -> Database:
    return Database(get_settings())


def get_active_provider(db: Database) -> BasePaymentProvider:
    """Активный провайдер (`.env` `PAYMENT_PROVIDER`) — тонкая обёртка над
    app.payments.factory (единая точка резолва для каналов и фоновых задач
    backend, см. DECISIONS.md). Использовать ТОЛЬКО для создания НОВЫХ
    платежей — для проверки статуса существующего платежа см.
    `get_provider_for_type`."""
    return payment_factory.get_active_provider(db, get_settings())


def get_provider_for_type(provider_type: PaymentProviderType) -> BasePaymentProvider:
    """Провайдер конкретного типа, без учёта текущего активного/override —
    для сверки статуса УЖЕ созданного платежа (п.9.3 ТЗ: "уже созданный платёж
    дообрабатывается тем банком, через который был создан", независимо от того,
    какой провайдер активен для НОВЫХ платежей сейчас). См. DECISIONS.md.
    `db` передаётся на случай `RequisitesQrProvider.check_status`, которому нужен
    доступ к БД для разовой сверки по запросу пользователя."""
    return payment_factory.create_provider(get_settings(), provider_type, db=get_channel_db())


QUANTITY_OPTIONS: tuple[int, ...] = (1, 3, 5, 10)


class PurchaseStates(StatesGroup):
    choosing_giveaway = State()
    choosing_quantity = State()
    awaiting_phone_for_gift = State()
    confirming_order = State()


class RegistrationStates(StatesGroup):
    awaiting_phone = State()
    awaiting_name = State()
