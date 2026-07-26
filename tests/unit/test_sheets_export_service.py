"""Тесты автовыгрузки номерков в Google Sheets (вне ТЗ, см. DECISIONS.md №44).

Google Sheets API не вызывается по-настоящему — подставляется фейковый клиент,
записывающий аргументы вызова `values().update()`.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.db import Database
from app.models.enums import TicketSource
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.ticket import Ticket
from app.models.ticket_pool import TicketPool
from app.services import sheets_export_service as svc
from sqlalchemy.orm import Session


class FakeSheetsClient:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, Any]] = []

    def spreadsheets(self) -> FakeSheetsClient:
        return self

    def values(self) -> FakeSheetsClient:
        return self

    def update(
        self, *, spreadsheetId: str, range: str, valueInputOption: str, body: dict[str, Any]
    ) -> FakeSheetsClient:
        self.update_calls.append({"spreadsheetId": spreadsheetId, "range": range, "body": body})
        return self

    def execute(self) -> dict[str, Any]:
        return {}


def make_giveaway(session: Session, *, google_sheet_id: str | None = None) -> Giveaway:
    g = Giveaway(
        name="Sheets Test",
        prefix="SHT",
        ticket_price=10000,
        max_tickets=100,
        google_sheet_id=google_sheet_id,
    )
    session.add(g)
    session.flush()
    return g


def make_ticket(session: Session, giveaway: Giveaway, *, number: int, phone: str) -> Ticket:
    participant = Participant(phone=phone)
    session.add(participant)
    session.flush()
    pool_row = TicketPool(
        giveaway_id=giveaway.id, number=number, shuffle_order=number, status="issued"
    )
    session.add(pool_row)
    session.flush()
    ticket = Ticket(
        giveaway_id=giveaway.id,
        pool_id=pool_row.id,
        number=number,
        full_code=giveaway.format_code(number),
        participant_id=participant.id,
        source=TicketSource.ONLINE,
    )
    session.add(ticket)
    session.flush()
    return ticket


def test_export_giveaway_tickets_masks_phone_and_orders_by_number(session: Session) -> None:
    giveaway = make_giveaway(session, google_sheet_id="sheet-1")
    make_ticket(session, giveaway, number=2, phone="79990002222")
    make_ticket(session, giveaway, number=1, phone="79990001111")
    session.flush()

    client = FakeSheetsClient()
    svc.export_giveaway_tickets(session, client, giveaway)

    assert len(client.update_calls) == 1
    call = client.update_calls[0]
    assert call["spreadsheetId"] == "sheet-1"
    values = call["body"]["values"]
    assert values[0] == svc._HEADER
    numbers = [row[0] for row in values[1:]]
    assert numbers == [1, 2]
    assert values[1][2] == "••••••01111"
    assert values[2][2] == "••••••02222"


def test_export_giveaway_tickets_skips_without_sheet_id(session: Session) -> None:
    giveaway = make_giveaway(session, google_sheet_id=None)
    make_ticket(session, giveaway, number=1, phone="79990001111")
    session.flush()

    client = FakeSheetsClient()
    svc.export_giveaway_tickets(session, client, giveaway)

    assert client.update_calls == []


def test_sync_all_giveaways_noop_without_credentials(db: Database, settings: Settings) -> None:
    with db.session() as session:
        giveaway = make_giveaway(session, google_sheet_id="sheet-x")
        make_ticket(session, giveaway, number=1, phone="79990001111")

    assert settings.google_sheets_credentials_file == ""
    svc.sync_all_giveaways(db, settings)  # не должно упасть и не должно ничего вызвать


def test_sync_all_giveaways_skips_unchanged_giveaway(
    db: Database, settings: Settings, monkeypatch: Any
) -> None:
    svc._last_synced_count.clear()
    client = FakeSheetsClient()
    monkeypatch.setattr(svc, "build_sheets_client", lambda _settings: client)

    with db.session() as session:
        giveaway = make_giveaway(session, google_sheet_id="sheet-y")
        make_ticket(session, giveaway, number=1, phone="79990001111")
        giveaway.tickets_issued = 1
        session.flush()
        giveaway_id = giveaway.id

    svc.sync_all_giveaways(db, settings)
    assert len(client.update_calls) == 1

    svc.sync_all_giveaways(db, settings)
    assert len(client.update_calls) == 1  # tickets_issued не изменился -> без повторного вызова

    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
        assert giveaway is not None
        giveaway.tickets_issued = 2

    svc.sync_all_giveaways(db, settings)
    assert len(client.update_calls) == 2
