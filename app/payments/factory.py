"""Фабрика платёжных провайдеров (п.9.3, 9.4, 5.4.2 ТЗ).

Активный провайдер определяется `.env` (`PAYMENT_PROVIDER`), но приоритетнее —
`PlatformSettings.payment_provider_override` из панели Super Admin (если задан).
Переключение не требует переразвёртывания: фабрика вызывается заново при каждом
использовании (без кеша уровня процесса на выбор типа, только сами клиенты можно
кэшировать по типу при желании).
"""

from __future__ import annotations

from app.core.config import Settings
from app.models.enums import PaymentProviderType
from app.payments.base import BasePaymentProvider
from app.payments.mock import MockProvider
from app.payments.tbank import TBankProvider
from app.payments.vtb import VTBProvider

_REGISTRY: dict[PaymentProviderType, type[BasePaymentProvider]] = {
    PaymentProviderType.MOCK: MockProvider,
    PaymentProviderType.TBANK: TBankProvider,
    PaymentProviderType.VTB: VTBProvider,
}


def resolve_provider_type(
    settings: Settings, *, override: PaymentProviderType | None
) -> PaymentProviderType:
    """override (PlatformSettings.payment_provider_override) приоритетнее .env (п.9.3 ТЗ)."""
    if override is not None:
        return override
    return PaymentProviderType(settings.payment_provider)


def create_provider(settings: Settings, provider_type: PaymentProviderType) -> BasePaymentProvider:
    if provider_type == PaymentProviderType.MOCK:
        return MockProvider(settings)
    if provider_type == PaymentProviderType.TBANK:
        return TBankProvider.from_settings(settings)
    if provider_type == PaymentProviderType.VTB:
        return VTBProvider.from_settings(settings)
    raise ValueError(f"Неизвестный тип платёжного провайдера: {provider_type}")


def get_provider(
    settings: Settings, *, override: PaymentProviderType | None = None
) -> BasePaymentProvider:
    provider_type = resolve_provider_type(settings, override=override)
    return create_provider(settings, provider_type)
