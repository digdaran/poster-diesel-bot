"""Тесты `channels/telegram/state.py::get_provider_for_type` — резолв провайдера
конкретного платежа (см. DECISIONS.md), а не текущего активного, для сверки
статуса уже созданного платежа (п.9.3 ТЗ)."""

from __future__ import annotations

import pytest
from app.models.enums import PaymentProviderType
from app.payments.requisites_qr import RequisitesQrProvider

from channels.telegram.state import get_provider_for_type


def test_get_provider_for_type_returns_requested_type() -> None:
    provider = get_provider_for_type(PaymentProviderType.REQUISITES_QR)
    assert isinstance(provider, RequisitesQrProvider)


def test_get_provider_for_type_raises_for_removed_acquiring_provider() -> None:
    """`MOCK`/`TBANK`/`VTB` остаются в `PaymentProviderType` только ради уже
    существующих исторических строк `Payment.provider` в БД (интернет-эквайринг
    удалён, см. DECISIONS.md №44) — конструировать эти провайдеры больше нельзя."""
    with pytest.raises(ValueError):
        get_provider_for_type(PaymentProviderType.MOCK)
