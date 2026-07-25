"""Раздел «Настройки» (п.7.1, 9.3, 11.4, 12 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.enums import AuditActorType
from app.models.panel_user import PanelUser
from app.services import audit_service
from app.services import settings_service as svc
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import (
    IgnorePhoneVerificationUpdateRequest,
    PlatformSettingsOut,
    SupportContactsUpdateRequest,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=PlatformSettingsOut)
def get_settings(
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.SETTINGS_VIEW)),
) -> PlatformSettingsOut:
    settings = svc.get_or_create_settings(session)
    return PlatformSettingsOut(
        ignore_phone_verification=settings.ignore_phone_verification,
        online_status_poll_interval_sec=settings.online_status_poll_interval_sec,
        online_status_poll_max_attempts=settings.online_status_poll_max_attempts,
        online_reservation_ttl_sec=settings.online_reservation_ttl_sec,
        manual_reservation_ttl_sec=settings.manual_reservation_ttl_sec,
        support_contacts=settings.support_contacts,
    )


@router.patch("/support-contacts", response_model=PlatformSettingsOut)
def update_support_contacts(
    payload: SupportContactsUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.SETTINGS_EDIT_SUPPORT_CONTACTS)),
) -> PlatformSettingsOut:
    svc.update_support_contacts(
        session, support_contacts=payload.support_contacts, updated_by=user.id
    )
    audit_service.log(
        session,
        action="settings_update_support_contacts",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        ip_address=request.client.host if request.client else None,
    )
    return get_settings(session=session, _user=user)


@router.patch("/ignore-phone-verification", response_model=PlatformSettingsOut)
def update_ignore_phone_verification(
    payload: IgnorePhoneVerificationUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.IGNORE_PHONE_VERIFICATION_TOGGLE)),
) -> PlatformSettingsOut:
    """Переключатель ignore_phone_verification — только Super Admin (п.7.1, 12 ТЗ).
    Изменение фиксируется в аудите."""
    svc.update_ignore_phone_verification(
        session, ignore_phone_verification=payload.ignore_phone_verification, updated_by=user.id
    )
    audit_service.log(
        session,
        action="settings_toggle_ignore_phone_verification",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        details={"ignore_phone_verification": payload.ignore_phone_verification},
        ip_address=request.client.host if request.client else None,
    )
    return get_settings(session=session, _user=user)
