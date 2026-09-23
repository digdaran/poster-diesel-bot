"""TLS-доверие к T-API выписки: `business.tbank.ru` подписан УЦ Минцифры, которого
нет в `certifi` (боевой инцидент 2026-09-23, DECISIONS_LOG.md №83)."""

from __future__ import annotations

import datetime as dt
import hashlib
import ssl
from typing import Any

import certifi
import httpx
import pytest
from app.payments import tbank_statement
from app.payments.tbank_statement import TBankStatementProvider, _tbank_ssl_context

# Отпечаток корня, сверенный по трём независимым источникам (Госуслуги, корень
# цепочки business.tbank.ru, хранилище хоста прода) — защита от подмены файла.
_EXPECTED_ROOT_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def _bundled_root_der() -> bytes:
    text = tbank_statement._RUSSIAN_TRUSTED_ROOT_CA.read_text(encoding="ascii")
    return ssl.PEM_cert_to_DER_cert(text[text.index("-----BEGIN CERTIFICATE-----") :])


def test_bundled_root_ca_matches_pinned_fingerprint() -> None:
    assert hashlib.sha256(_bundled_root_der()).hexdigest() == _EXPECTED_ROOT_SHA256


def test_ssl_context_trusts_russian_root_and_keeps_certifi() -> None:
    context = _tbank_ssl_context()
    trusted_der = {bytes(der) for der in context.get_ca_certs(binary_form=True)}

    assert _bundled_root_der() in trusted_der

    certifi_only = ssl.create_default_context(cafile=certifi.where())
    certifi_der = {bytes(der) for der in certifi_only.get_ca_certs(binary_form=True)}
    assert certifi_der <= trusted_der
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_fetch_operations_uses_tbank_ssl_context(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(200, json={"operations": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(tbank_statement.httpx, "get", fake_get)
    provider = TBankStatementProvider(
        api_base="https://business.tbank.ru/openapi/api/v1",
        api_token="token",
        account_number="40702810000000000000",
    )

    assert provider.fetch_operations(since=dt.datetime(2026, 9, 1)) == []
    assert captured["verify"] is _tbank_ssl_context()
