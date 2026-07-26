"""Тест маскирования телефона для публичных выгрузок (см. DECISIONS.md №44)."""

from __future__ import annotations

from app.core.phone import mask_phone


def test_mask_phone_shows_only_last_five_digits() -> None:
    assert mask_phone("79991234567") == "••••••34567"


def test_mask_phone_shorter_than_visible_is_fully_masked() -> None:
    assert mask_phone("123") == "•••"


def test_mask_phone_custom_visible_digits() -> None:
    assert mask_phone("79991234567", visible_digits=2) == "•••••••••67"
