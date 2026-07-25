"""Фабрика платёжных провайдеров (п.9.3, 9.4, 5.4.2 ТЗ).

Единственный активный провайдер — `RequisitesQrProvider` (`.env`
`PAYMENT_PROVIDER`, см. DECISIONS_LOG.md №37). Переключатель провайдера в панели
Super Admin (`PlatformSettings.payment_provider_override`) удалён вместе с
интернет-эквайрингом (DECISIONS_LOG.md №44) — переключать больше не между чем.

`MOCK`/`TBANK`/`VTB` остаются в `PaymentProviderType` только ради уже
существующих исторических строк `Payment.provider` в БД (см. DECISIONS_LOG.md
№44) — `create_provider` для них поднимает `ValueError`, конструировать эти
провайдеры больше нельзя.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.db import Database
from app.models.enums import PaymentProviderType
from app.payments.base import BasePaymentProvider
from app.payments.requisites_qr import RequisitesQrProvider


def create_provider(
    settings: Settings, provider_type: PaymentProviderType, *, db: Database | None = None
) -> BasePaymentProvider:
    if provider_type == PaymentProviderType.REQUISITES_QR:
        return RequisitesQrProvider.from_settings(settings, db=db)
    raise ValueError(f"Неизвестный тип платёжного провайдера: {provider_type}")


def get_active_provider(db: Database, settings: Settings) -> BasePaymentProvider:
    """Единая точка резолва активного провайдера для каналов и фоновых задач backend."""
    return create_provider(settings, PaymentProviderType(settings.payment_provider), db=db)
