"""Тесты app/services/poster_service.py — сохранение/удаление цифровых
постеров розыгрыша, загружаемых через веб-админку (см. DECISIONS.md №46)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.giveaway import Giveaway
from app.models.giveaway_poster import GiveawayPoster
from app.services import poster_service
from sqlalchemy import select


def make_giveaway(db: Database) -> int:
    with db.session() as session:
        giveaway = Giveaway(name="Test", prefix="PST", ticket_price=10000, max_tickets=10)
        session.add(giveaway)
        session.flush()
        return giveaway.id


def test_save_poster_writes_file_and_creates_row(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.poster_dir = str(tmp_path / "posters")  # type: ignore[misc]
    giveaway_id = make_giveaway(db)

    with db.session() as session:
        poster = poster_service.save_poster(
            session,
            settings,
            giveaway_id=giveaway_id,
            content=b"fake-png-bytes",
            content_type="image/png",
            original_filename="poster.png",
        )
        poster_id = poster.id

    with db.session() as session:
        rows = list(
            session.execute(
                select(GiveawayPoster).where(GiveawayPoster.giveaway_id == giveaway_id)
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].id == poster_id
    assert Path(rows[0].file_path).exists()
    assert Path(rows[0].file_path).read_bytes() == b"fake-png-bytes"
    assert Path(rows[0].file_path).suffix == ".png"


def test_save_poster_rejects_disallowed_content_type(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.poster_dir = str(tmp_path / "posters")  # type: ignore[misc]
    giveaway_id = make_giveaway(db)

    with db.session() as session, pytest.raises(ValueError, match="Недопустимый тип файла"):
        poster_service.save_poster(
            session,
            settings,
            giveaway_id=giveaway_id,
            content=b"%PDF-fake",
            content_type="application/pdf",
            original_filename="poster.pdf",
        )


def test_save_poster_rejects_oversized_content(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.poster_dir = str(tmp_path / "posters")  # type: ignore[misc]
    giveaway_id = make_giveaway(db)

    with db.session() as session, pytest.raises(ValueError, match="превышает лимит"):
        poster_service.save_poster(
            session,
            settings,
            giveaway_id=giveaway_id,
            content=b"x" * (10 * 1024 * 1024 + 1),
            content_type="image/png",
            original_filename="poster.png",
        )


def test_delete_poster_removes_file_from_disk(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    settings.poster_dir = str(tmp_path / "posters")  # type: ignore[misc]
    giveaway_id = make_giveaway(db)

    with db.session() as session:
        poster = poster_service.save_poster(
            session,
            settings,
            giveaway_id=giveaway_id,
            content=b"data",
            content_type="image/jpeg",
            original_filename=None,
        )
        file_path = Path(poster.file_path)

    assert file_path.exists()
    with db.session() as session:
        poster = session.get(GiveawayPoster, poster.id)
        assert poster is not None
        poster_service.delete_poster(poster=poster)
        session.delete(poster)

    assert not file_path.exists()
    with db.session() as session:
        assert session.get(GiveawayPoster, poster.id) is None
