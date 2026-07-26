"""Автовыгрузка выданных номерков в Google Sheets, по розыгрышу (см. DECISIONS.md).

Своя Google-таблица на каждый розыгрыш (`Giveaway.google_sheet_id`), т.к. ссылка на
неё раздаётся публично участникам именно этого розыгрыша — общий лист на все
розыгрыши раскрывал бы данные чужих розыгрышей всем. Администратор создаёт
таблицу сам и заранее выдаёт сервисному аккаунту (см. `GOOGLE_SHEETS_CREDENTIALS_FILE`)
доступ редактора — платформа доступ не запрашивает и таблицу не создаёт.

Полное перезатирание диапазона при каждой синхронизации (не построчный patch) —
номерки в этой версии никогда не отзываются (см. ТЗ §21: отмена подтверждённых
ручных регистраций не реализуется, платежи не возвращаются), поэтому список только
растёт и полная перезапись не теряет данные и не требует отдельной логики очистки
"хвостов". `_last_synced_count` — небольшая оптимизация, чтобы не дёргать Sheets API
на розыгрышах, где с прошлого тика ничего не выдавалось.

Синхронизация не пишет ничего в нашу БД и не меняет доменное состояние — это не
"мутирующее действие" в смысле audit_service, поэтому строк в audit_log не
создаёт (в отличие от `giveaway.google_sheet_id`, который меняется через панель
и audit-логируется в `backend/api/giveaways.py`).
"""

from __future__ import annotations

from typing import Any

import structlog
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import Database
from app.core.phone import mask_phone
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.ticket import Ticket

logger = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_SHEET_RANGE = "A1"
_HEADER = ["№ номерка", "Код", "Телефон", "Выдан"]

_last_synced_count: dict[int, int] = {}


def build_sheets_client(settings: Settings) -> Any | None:
    """Возвращает Sheets API resource, либо `None`, если функция не настроена
    (пустой `GOOGLE_SHEETS_CREDENTIALS_FILE`) или файл ключа сервисного аккаунта
    не читается."""
    path = settings.google_sheets_credentials_path
    if path is None:
        return None
    try:
        credentials = Credentials.from_service_account_file(str(path), scopes=_SCOPES)
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except Exception:
        logger.exception("google_sheets_credentials_load_failed", path=str(path))
        return None


def _ticket_rows(session: Session, giveaway_id: int) -> list[list[Any]]:
    stmt = (
        select(Ticket.number, Ticket.full_code, Participant.phone, Ticket.created_at)
        .join(Participant, Participant.id == Ticket.participant_id)
        .where(Ticket.giveaway_id == giveaway_id)
        .order_by(Ticket.number)
    )
    rows = session.execute(stmt).all()
    return [
        [number, full_code, mask_phone(phone), created_at.isoformat()]
        for number, full_code, phone, created_at in rows
    ]


def export_giveaway_tickets(session: Session, sheets_client: Any, giveaway: Giveaway) -> None:
    if not giveaway.google_sheet_id:
        return
    rows = _ticket_rows(session, giveaway.id)
    sheets_client.spreadsheets().values().update(
        spreadsheetId=giveaway.google_sheet_id,
        range=_SHEET_RANGE,
        valueInputOption="RAW",
        body={"values": [_HEADER, *rows]},
    ).execute()


def sync_all_giveaways(db: Database, settings: Settings) -> None:
    sheets_client = build_sheets_client(settings)
    if sheets_client is None:
        return

    with db.session() as session:
        giveaways = list(
            session.execute(select(Giveaway).where(Giveaway.google_sheet_id.is_not(None))).scalars()
        )
        for giveaway in giveaways:
            if _last_synced_count.get(giveaway.id) == giveaway.tickets_issued:
                continue
            try:
                export_giveaway_tickets(session, sheets_client, giveaway)
                _last_synced_count[giveaway.id] = giveaway.tickets_issued
            except Exception:
                logger.exception("google_sheets_export_failed", giveaway_id=giveaway.id)
