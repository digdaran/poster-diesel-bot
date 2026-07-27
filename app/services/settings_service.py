"""Глобальные настройки платформы (PlatformSettings, singleton, п.6.2 ТЗ)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.platform_settings import SINGLETON_ID, PlatformSettings


def get_or_create_settings(session: Session) -> PlatformSettings:
    settings = session.get(PlatformSettings, SINGLETON_ID)
    if settings is None:
        settings = PlatformSettings(id=SINGLETON_ID)
        session.add(settings)
        session.flush()
    return settings


def update_support_contacts(
    session: Session, *, support_contacts: dict, updated_by: int
) -> PlatformSettings:
    settings = get_or_create_settings(session)
    settings.support_contacts = support_contacts
    settings.updated_at = utcnow()
    settings.updated_by = updated_by
    session.flush()
    return settings


def update_ignore_phone_verification(
    session: Session, *, ignore_phone_verification: bool, updated_by: int
) -> PlatformSettings:
    settings = get_or_create_settings(session)
    settings.ignore_phone_verification = ignore_phone_verification
    settings.updated_at = utcnow()
    settings.updated_by = updated_by
    session.flush()
    return settings


def support_contact_url(
    contacts: dict[str, Any], platform: Literal["telegram", "vk"]
) -> str | None:
    """Строит кликабельную ссылку «Написать в поддержку» из `support_contacts`
    по зарезервированному ключу канала (см. DECISIONS_LOG.md №50). Админ может
    указать в веб-панели готовую ссылку (https://...) либо просто
    username/id — в этом случае ссылка достраивается автоматически."""
    value = contacts.get(platform)
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    handle = value.lstrip("@")
    return f"https://t.me/{handle}" if platform == "telegram" else f"https://vk.com/{handle}"
