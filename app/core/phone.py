"""Нормализация номера телефона (п.7.1 ТЗ).

Единый нормализованный формат — только цифры с кодом страны, без `+`
(например `79991234567`). Используется как ключ участника (`Participant.phone`).
"""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException

DEFAULT_REGION = "RU"
_FALLBACK_RE = re.compile(r"^(?:\+?7|8)?(\d{10})$")


class InvalidPhoneError(ValueError):
    """Номер телефона не проходит нормализацию/валидацию."""


def normalize_phone(raw: str, *, default_region: str = DEFAULT_REGION) -> str:
    """Нормализует произвольный ввод номера телефона к формату `<country_code><subscriber>`.

    Пытается сначала честно распарсить через `phonenumbers` (Google libphonenumber).
    Если это не удаётся (реальные пользователи иногда вводят нестандартно —
    см. DECISIONS.md, п.13), применяет упрощённый фолбэк для российских номеров
    (10 цифр, опциональный префикс `+7`/`7`/`8`).

    Raises:
        InvalidPhoneError: если номер невозможно нормализовать ни одним из способов.
    """
    stripped = raw.strip()
    if not stripped:
        raise InvalidPhoneError("Пустой номер телефона")

    try:
        parsed = phonenumbers.parse(stripped, default_region)
        if phonenumbers.is_valid_number(parsed):
            return f"{parsed.country_code}{parsed.national_number}"
    except NumberParseException:
        pass

    digits_only = re.sub(r"\D", "", stripped)
    match = _FALLBACK_RE.match(digits_only) or _FALLBACK_RE.match(stripped)
    if match:
        return f"7{match.group(1)}"

    raise InvalidPhoneError(f"Не удалось нормализовать номер телефона: {raw!r}")


def is_valid_phone(raw: str, *, default_region: str = DEFAULT_REGION) -> bool:
    try:
        normalize_phone(raw, default_region=default_region)
        return True
    except InvalidPhoneError:
        return False
