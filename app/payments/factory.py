"""Фабрика платёжных провайдеров (п.9.3, 9.4, 5.4.2 ТЗ).

Активный провайдер определяется `.env` (`PAYMENT_PROVIDER`), но приоритетнее —
`PlatformSettings.payment_provider_override` из панели Super Admin (если задан).
Переключение не требует переразвёртывания: фабрика вызывается заново при каждом
использовании (без кеша уровня процесса на выбор типа, только сами клиенты можно
кэшировать по типу при желании).
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.db import Database
from app.models.enums import PaymentProviderType
from app.payments.base import BasePaymentProvider
from app.payments.mock import MockProvider
from app.payments.requisites_qr import RequisitesQrProvider
from app.payments.tbank import TBankProvider
from app.payments.vtb import VTBProvider
from app.services import settings_service

_REGISTRY: dict[PaymentProviderType, type[BasePaymentProvider]] = {
    PaymentProviderType.MOCK: MockProvider,
    PaymentProviderType.TBANK: TBankProvider,
    PaymentProviderType.VTB: VTBProvider,
    PaymentProviderType.REQUISITES_QR: RequisitesQrProvider,
}


def resolve_provider_type(
    settings: Settings, *, override: PaymentProviderType | None
) -> PaymentProviderType:
    """override (PlatformSettings.payment_provider_override) приоритетнее .env (п.9.3 ТЗ)."""
    if override is not None:
        return override
    return PaymentProviderType(settings.payment_provider)


def create_provider(
    settings: Settings, provider_type: PaymentProviderType, *, db: Database | None = None
) -> BasePaymentProvider:
    if provider_type == PaymentProviderType.MOCK:
        return MockProvider(settings)
    if provider_type == PaymentProviderType.TBANK:
        return TBankProvider.from_settings(settings)
    if provider_type == PaymentProviderType.VTB:
        return VTBProvider.from_settings(settings)
    if provider_type == PaymentProviderType.REQUISITES_QR:
        return RequisitesQrProvider.from_settings(settings, db=db)
    raise ValueError(f"Неизвестный тип платёжного провайдера: {provider_type}")


def get_provider(
    settings: Settings,
    *,
    override: PaymentProviderType | None = None,
    db: Database | None = None,
) -> BasePaymentProvider:
    provider_type = resolve_provider_type(settings, override=override)
    return create_provider(settings, provider_type, db=db)


def get_active_provider(db: Database, settings: Settings) -> BasePaymentProvider:
    """Провайдер с учётом приоритета `PlatformSettings.payment_provider_override`
    над `.env` (п.9.3 ТЗ) — единая точка резолва для каналов и фоновых задач backend."""
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
        override = platform_settings.payment_provider_override
    return get_provider(settings, override=override, db=db)
