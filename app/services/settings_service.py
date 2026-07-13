"""Глобальные настройки платформы (PlatformSettings, singleton, п.6.2 ТЗ)."""

from __future__ import annotations

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


def update_payment_provider_override(
    session: Session, *, payment_provider_override: str | None, updated_by: int
) -> PlatformSettings:
    from app.models.enums import PaymentProviderType

    settings = get_or_create_settings(session)
    settings.payment_provider_override = (
        PaymentProviderType(payment_provider_override) if payment_provider_override else None
    )
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
