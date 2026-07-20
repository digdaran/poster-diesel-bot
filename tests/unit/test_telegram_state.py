"""Тесты `channels/telegram/state.py::get_provider_for_type` — резолв провайдера
конкретного платежа (см. DECISIONS.md), а не текущего активного/override, для
сверки статуса уже созданного платежа (п.9.3 ТЗ)."""

from __future__ import annotations

from app.models.enums import PaymentProviderType
from app.payments.mock import MockProvider

from channels.telegram.state import get_provider_for_type


def test_get_provider_for_type_returns_requested_type() -> None:
    """Резолв по явному типу — не читает `PlatformSettings.payment_provider_override`,
    в отличие от `get_active_provider`."""
    provider = get_provider_for_type(PaymentProviderType.MOCK)
    assert isinstance(provider, MockProvider)
