"""Именованные разрешения и матрица прав ролей (п.11.2, 11.3 ТЗ).

Единственный источник истины по правам доступа. FastAPI-эндпоинты проверяют права
через зависимость `require_permission` (см. backend/api/deps.py), которая ссылается
на `ROLE_PERMISSIONS` из этого модуля. Frontend использует тот же список разрешений
(транслируется в /auth/me) только для UX (скрытие пунктов меню) — не для защиты.
"""

from __future__ import annotations

from enum import Enum


class PanelRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMINISTRATOR = "administrator"
    OPERATOR = "operator"


class Permission(str, Enum):
    # Просмотр (доступен всем трём ролям)
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_PARTICIPANTS = "view_participants"
    VIEW_SALES = "view_sales"
    VIEW_TICKETS = "view_tickets"
    VIEW_GIVEAWAYS = "view_giveaways"

    # Ручные регистрации — доступны всем трём ролям
    MANUAL_REGISTRATION_CREATE = "manual_registration_create"
    MANUAL_REGISTRATION_CONFIRM = "manual_registration_confirm"
    MANUAL_REGISTRATION_CANCEL = "manual_registration_cancel"

    # Super Admin + Administrator
    PARTICIPANT_EDIT = "participant_edit"
    PARTICIPANT_BLOCK = "participant_block"
    GIVEAWAY_EDIT = "giveaway_edit"
    GIVEAWAY_LOCK = "giveaway_lock"
    SETTINGS_VIEW = "settings_view"
    SETTINGS_EDIT_SUPPORT_CONTACTS = "settings_edit_support_contacts"
    BROADCAST_VIEW = "broadcast_view"
    BROADCAST_SEND = "broadcast_send"
    REPORTS_VIEW = "reports_view"
    REPORTS_EXPORT = "reports_export"
    AUDIT_VIEW = "audit_view"
    SALES_EXPORT = "sales_export"
    VIEW_BANK_RECONCILIATION = "view_bank_reconciliation"

    # Только Super Admin
    PANEL_USERS_MANAGE = "panel_users_manage"
    IGNORE_PHONE_VERIFICATION_TOGGLE = "ignore_phone_verification_toggle"


_VIEW_ALL = {
    Permission.VIEW_DASHBOARD,
    Permission.VIEW_PARTICIPANTS,
    Permission.VIEW_SALES,
    Permission.VIEW_TICKETS,
    Permission.VIEW_GIVEAWAYS,
    Permission.MANUAL_REGISTRATION_CREATE,
    Permission.MANUAL_REGISTRATION_CONFIRM,
    Permission.MANUAL_REGISTRATION_CANCEL,
}

_ADMINISTRATOR_EXTRA = {
    Permission.PARTICIPANT_EDIT,
    Permission.PARTICIPANT_BLOCK,
    Permission.GIVEAWAY_EDIT,
    Permission.GIVEAWAY_LOCK,
    Permission.SETTINGS_VIEW,
    Permission.SETTINGS_EDIT_SUPPORT_CONTACTS,
    Permission.BROADCAST_VIEW,
    Permission.BROADCAST_SEND,
    Permission.REPORTS_VIEW,
    Permission.REPORTS_EXPORT,
    Permission.AUDIT_VIEW,
    Permission.SALES_EXPORT,
    Permission.VIEW_BANK_RECONCILIATION,
}

_SUPER_ADMIN_EXTRA = {
    Permission.PANEL_USERS_MANAGE,
    Permission.IGNORE_PHONE_VERIFICATION_TOGGLE,
}

ROLE_PERMISSIONS: dict[PanelRole, frozenset[Permission]] = {
    PanelRole.OPERATOR: frozenset(_VIEW_ALL),
    PanelRole.ADMINISTRATOR: frozenset(_VIEW_ALL | _ADMINISTRATOR_EXTRA),
    PanelRole.SUPER_ADMIN: frozenset(_VIEW_ALL | _ADMINISTRATOR_EXTRA | _SUPER_ADMIN_EXTRA),
}


def role_has_permission(role: PanelRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def permissions_for_role(role: PanelRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]
