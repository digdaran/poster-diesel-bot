"""Тесты ограничений целостности моделей (п.6.2 ТЗ)."""

from __future__ import annotations

import pytest
from app.models import ChannelBinding, ChannelType, Giveaway, PanelUser, PanelUserRole, Participant
from app.models.base import utcnow
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def make_participant(session: Session, phone: str = "79991234567") -> Participant:
    p = Participant(phone=phone, phone_verified=False)
    session.add(p)
    session.flush()
    return p


def test_participant_phone_is_unique(session: Session) -> None:
    make_participant(session, "79991234567")
    session.flush()
    with pytest.raises(IntegrityError):
        make_participant(session, "79991234567")
    session.rollback()


def test_channel_binding_unique_external_id_per_channel(session: Session) -> None:
    p1 = make_participant(session, "79991111111")
    p2 = make_participant(session, "79992222222")
    session.add(
        ChannelBinding(
            participant_id=p1.id,
            channel=ChannelType.TELEGRAM,
            external_user_id="tg-1",
            phone_verified=True,
            linked_at=utcnow(),
        )
    )
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(
            ChannelBinding(
                participant_id=p2.id,
                channel=ChannelType.TELEGRAM,
                external_user_id="tg-1",  # тот же внешний id в том же канале
                phone_verified=True,
                linked_at=utcnow(),
            )
        )
        session.flush()
    session.rollback()


def test_channel_binding_at_most_one_per_channel_per_participant(session: Session) -> None:
    p = make_participant(session)
    session.add(
        ChannelBinding(
            participant_id=p.id,
            channel=ChannelType.TELEGRAM,
            external_user_id="tg-1",
            phone_verified=True,
            linked_at=utcnow(),
        )
    )
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.TELEGRAM,
                external_user_id="tg-2",
                phone_verified=True,
                linked_at=utcnow(),
            )
        )
        session.flush()
    session.rollback()


def test_participant_phone_verified_recomputed_from_bindings(session: Session) -> None:
    p = make_participant(session)
    assert p.phone_verified is False
    p.channel_bindings.append(
        ChannelBinding(
            participant_id=p.id,
            channel=ChannelType.TELEGRAM,
            external_user_id="tg-1",
            phone_verified=True,
            linked_at=utcnow(),
        )
    )
    session.flush()
    p.recompute_phone_verified()
    assert p.phone_verified is True


def test_giveaway_prefix_unique(session: Session) -> None:
    session.add(Giveaway(name="A", prefix="AUG", ticket_price=10000, max_tickets=100))
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(Giveaway(name="B", prefix="AUG", ticket_price=20000, max_tickets=50))
        session.flush()
    session.rollback()


def test_giveaway_format_code() -> None:
    g = Giveaway(name="A", prefix="AUG", ticket_price=10000, max_tickets=100)
    assert g.format_code(42) == "AUG-000042"
    assert g.format_code(1) == "AUG-000001"


def test_giveaway_free_tickets_and_open_for_sale() -> None:
    g = Giveaway(
        name="A",
        prefix="AUG",
        ticket_price=10000,
        max_tickets=10,
        tickets_issued=3,
        tickets_reserved=2,
        is_registration_open=True,
        is_locked=False,
    )
    assert g.free_tickets_count == 5
    assert g.is_open_for_sale is True
    g.is_locked = True
    assert g.is_open_for_sale is False


def test_panel_user_login_unique(session: Session) -> None:
    session.add(PanelUser(login="admin", password_hash="x", role=PanelUserRole.SUPER_ADMIN))
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(PanelUser(login="admin", password_hash="y", role=PanelUserRole.OPERATOR))
        session.flush()
    session.rollback()
