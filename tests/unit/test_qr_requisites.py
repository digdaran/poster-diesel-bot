"""Тесты сборки QR-payload по ГОСТ Р 56042-2014 (формат ST00012, п.20.1 ТЗ —
подтверждение платежа, DECISIONS.md)."""

from __future__ import annotations

import datetime as dt

from app.payments.qr_requisites import build_purpose, build_st00012_payload, vat_clause


def test_build_st00012_payload_field_order_and_values() -> None:
    payload = build_st00012_payload(
        name="ООО «Ромашка»",
        personal_acc="40702810900000000000",
        bank_name="АО «Т-Банк»",
        bic="044525974",
        corresp_acc="30101810145250000974",
        payee_inn="7700000000",
        kpp="770001001",
        sum_kopecks=150000,
        purpose="Оплата по счету № ABC-00001 от 22.07.2026",
    )
    assert payload.startswith("ST00012|")
    assert payload == (
        "ST00012|Name=ООО «Ромашка»|PersonalAcc=40702810900000000000|"
        "BankName=АО «Т-Банк»|BIC=044525974|CorrespAcc=30101810145250000974|"
        "PayeeINN=7700000000|KPP=770001001|Sum=150000|"
        "Purpose=Оплата по счету № ABC-00001 от 22.07.2026"
    )


def test_build_st00012_payload_omits_kpp_when_absent() -> None:
    payload = build_st00012_payload(
        name="ИП Иванов",
        personal_acc="40802810900000000000",
        bank_name="АО «Т-Банк»",
        bic="044525974",
        corresp_acc="30101810145250000974",
        payee_inn="770000000000",
        kpp=None,
        sum_kopecks=5000,
        purpose="Оплата по счету № XYZ-00042 от 22.07.2026",
    )
    assert "KPP=" not in payload
    assert "Sum=5000" in payload


def test_st00012_payload_encodes_as_utf8() -> None:
    """QR должен кодироваться в UTF-8 (см. DECISIONS.md) — префикс "ST00012" по
    ГОСТ Р 56042-2014 сам декларирует эту кодировку (версия "2" = UTF-8)."""
    payload = build_st00012_payload(
        name="ООО «Ромашка»",
        personal_acc="40702810900000000000",
        bank_name="АО «Т-Банк»",
        bic="044525974",
        corresp_acc="30101810145250000974",
        payee_inn="7700000000",
        kpp="770001001",
        sum_kopecks=150000,
        purpose="Оплата по счету № ABC-00001 от 22.07.2026",
    )
    encoded = payload.encode("utf-8")
    assert encoded.decode("utf-8") == payload


def test_vat_clause_zero_rate_means_not_taxed() -> None:
    assert vat_clause(150000, 0) == "НДС не облагается"


def test_vat_clause_computes_included_amount() -> None:
    # 1200.00 руб. включая НДС 20% -> НДС = 1200 * 20/120 = 200.00 руб.
    assert vat_clause(120000, 20) == "в т.ч. НДС 20% 200.00 руб."


def test_build_purpose_contains_invoice_number_and_date() -> None:
    purpose = build_purpose(
        invoice_no="ABC-00001",
        invoice_date=dt.date(2026, 7, 22),
        amount_kopecks=150000,
        vat_rate_percent=20,
    )
    assert "Оплата по счету № ABC-00001 от 22.07.2026" in purpose
    assert "НДС" in purpose
