"""Тесты `settings_service.support_contact_url` — построение кликабельной ссылки
«Написать в поддержку» из `PlatformSettings.support_contacts` (см. DECISIONS_LOG.md №50)."""

from __future__ import annotations

from app.services.settings_service import support_contact_url


def test_telegram_username_without_at_builds_tme_link() -> None:
    assert (
        support_contact_url({"telegram": "support_user"}, "telegram") == "https://t.me/support_user"
    )


def test_telegram_username_with_at_strips_it() -> None:
    assert (
        support_contact_url({"telegram": "@support_user"}, "telegram")
        == "https://t.me/support_user"
    )


def test_full_url_is_passed_through_unchanged() -> None:
    url = "https://t.me/support_user"
    assert support_contact_url({"telegram": url}, "telegram") == url


def test_vk_handle_builds_vk_com_link() -> None:
    assert support_contact_url({"vk": "support_group"}, "vk") == "https://vk.com/support_group"


def test_missing_key_returns_none() -> None:
    assert support_contact_url({"telegram": "support_user"}, "vk") is None


def test_empty_dict_returns_none() -> None:
    assert support_contact_url({}, "telegram") is None


def test_blank_value_returns_none() -> None:
    assert support_contact_url({"telegram": "   "}, "telegram") is None
