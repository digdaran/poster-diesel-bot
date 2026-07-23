"""Общие зависимости VK-канала: БД, настройки, активный платёжный провайдер,
FSM-состояния диалога покупки — состав идентичен `channels/telegram/state.py`
(п.8.1, 10.2 ТЗ), т.к. бизнес-сценарий не зависит от мессенджера."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.db import Database
from app.models.enums import PaymentProviderType
from app.payments import factory as payment_factory
from app.payments.base import BasePaymentProvider
from vkbottle import BaseStateGroup


@lru_cache
def get_channel_db() -> Database:
    return Database(get_settings())


def get_active_provider(db: Database) -> BasePaymentProvider:
    """См. `channels.telegram.state.get_active_provider` — та же единая точка
    резолва провайдера (с учётом `PlatformSettings.payment_provider_override`),
    переиспользуемая всеми каналами и фоновыми задачами backend."""
    return payment_factory.get_active_provider(db, get_settings())


def get_provider_for_type(provider_type: PaymentProviderType) -> BasePaymentProvider:
    """См. `channels.telegram.state.get_provider_for_type` — сверка статуса УЖЕ
    созданного платежа тем банком, которым он был создан, а не текущим активным."""
    return payment_factory.create_provider(get_settings(), provider_type, db=get_channel_db())


QUANTITY_OPTIONS: tuple[int, ...] = (1, 3, 5, 10)


class PurchaseStates(BaseStateGroup):
    CHOOSING_GIVEAWAY = "purchase:choosing_giveaway"
    CHOOSING_QUANTITY = "purchase:choosing_quantity"
    AWAITING_PHONE_FOR_GIFT = "purchase:awaiting_phone_for_gift"
    CONFIRMING_ORDER = "purchase:confirming_order"


class RegistrationStates(BaseStateGroup):
    AWAITING_PHONE = "registration:awaiting_phone"
    AWAITING_NAME = "registration:awaiting_name"
