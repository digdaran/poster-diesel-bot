"""Построение QR-payload по ГОСТ Р 56042-2014 (формат ST00012) для оплаты по
банковским реквизитам — используется `RequisitesQrProvider` (app/payments/requisites_qr.py).

Чистые функции без сети/БД — только сборка строки и НДС-расчёт. Кодировка байт
результата — UTF-8: по ГОСТ Р 56042-2014 префикс "ST00012" сам декларирует
кодировку текста (версия "2" = UTF-8, версия "1" = Windows-1251), так что
байты QR обязаны ей соответствовать — см. DECISIONS.md; RECOMENDATION: перед
продакшеном реально отсканировать сгенерированный QR несколькими банковскими
приложениями.
"""

from __future__ import annotations

import datetime as dt


def vat_clause(amount_kopecks: int, vat_rate_percent: int) -> str:
    """Приписка о НДС для назначения платежа. Сумма считается включающей НДС
    (стандартная практика для назначений платежа в РФ)."""
    if vat_rate_percent <= 0:
        return "НДС не облагается"
    vat_kopecks = round(amount_kopecks * vat_rate_percent / (100 + vat_rate_percent))
    return f"в т.ч. НДС {vat_rate_percent}% {vat_kopecks / 100:.2f} руб."


def build_purpose(
    *, invoice_no: str, invoice_date: dt.date, amount_kopecks: int, vat_rate_percent: int
) -> str:
    return (
        f"Оплата по счету № {invoice_no} от {invoice_date:%d.%m.%Y}, "
        f"{vat_clause(amount_kopecks, vat_rate_percent)}"
    )


def build_st00012_payload(
    *,
    name: str,
    personal_acc: str,
    bank_name: str,
    bic: str,
    corresp_acc: str,
    payee_inn: str,
    kpp: str | None,
    sum_kopecks: int,
    purpose: str,
) -> str:
    """Собирает строку ST00012 (обычный unicode-текст — кодирование в
    UTF-8 для самого QR-изображения выполняется отдельно, на шаге рендера
    в канале, см. `channels/*/channel.py::send_qr_code` и DECISIONS.md)."""
    fields: dict[str, str] = {
        "Name": name,
        "PersonalAcc": personal_acc,
        "BankName": bank_name,
        "BIC": bic,
        "CorrespAcc": corresp_acc,
        "PayeeINN": payee_inn,
    }
    if kpp:
        fields["KPP"] = kpp
    fields["Sum"] = str(sum_kopecks)
    fields["Purpose"] = purpose

    return "ST00012|" + "|".join(f"{key}={value}" for key, value in fields.items())
