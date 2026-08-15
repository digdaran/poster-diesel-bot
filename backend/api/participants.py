"""Раздел «Участники» (п.11.4, 11.3, 14.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.permissions import Permission
from app.core.phone import InvalidPhoneError
from app.models.channel_binding import ChannelBinding
from app.models.enums import AuditActorType, ChannelType
from app.models.giveaway import TICKET_NUMBER_WIDTH, Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.ticket import Ticket
from app.services import audit_service, participant_service
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import ParticipantOut, ParticipantUpdateRequest

router = APIRouter(prefix="/participants", tags=["participants"])


def _tickets_by_participant(
    session: Session, participant_ids: list[int]
) -> dict[int, dict[str, str]]:
    """Номерки участников для экспорта, сгруппированные по коллекции
    (розыгрышу): `{"tickets": "ROMB: 000001, 000002; STAR: 000013",
    "giveaways": "Ромашка, Звезда"}`. Отдельный запрос, а не JOIN в основной
    выборке — иначе строки участников размножились бы по числу билетов."""
    if not participant_ids:
        return {}
    stmt = (
        select(Ticket.participant_id, Ticket.number, Giveaway.name, Giveaway.prefix)
        .join(Giveaway, Ticket.giveaway_id == Giveaway.id)
        .where(Ticket.participant_id.in_(participant_ids))
        .order_by(Giveaway.name, Ticket.number)
    )
    # participant_id -> prefix -> (giveaway_name, [номера])
    groups: dict[int, dict[str, tuple[str, list[str]]]] = {}
    for participant_id, number, giveaway_name, prefix in session.execute(stmt).all():
        by_prefix = groups.setdefault(participant_id, {})
        _, numbers = by_prefix.setdefault(prefix, (giveaway_name, []))
        numbers.append(f"{number:0{TICKET_NUMBER_WIDTH}d}")
    result: dict[int, dict[str, str]] = {}
    for participant_id, by_prefix in groups.items():
        result[participant_id] = {
            "tickets": "; ".join(
                f"{prefix}: {', '.join(numbers)}" for prefix, (_, numbers) in by_prefix.items()
            ),
            "giveaways": ", ".join(name for _, (name, _) in by_prefix.items()),
        }
    return result


@router.get("", response_model=None)
def list_participants(
    q: str | None = None,
    phone_verified: bool | None = None,
    is_blocked: bool | None = None,
    channel: ChannelType | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    total_tickets_min: int | None = None,
    total_tickets_max: int | None = None,
    active_tickets_min: int | None = None,
    active_tickets_max: int | None = None,
    page: int = 1,
    page_size: int = 50,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> dict[str, Any] | list[dict[str, Any]] | Response:
    validate_page_size(page_size)
    # Билеты за всё время / только в незаблокированных ("активных") акциях —
    # коррелированные скалярные подзапросы, а не JOIN + GROUP BY, чтобы не
    # плодить дубли строк участника при нескольких билетах/акциях.
    total_tickets_subq = (
        select(func.count(Ticket.id))
        .where(Ticket.participant_id == Participant.id)
        .correlate(Participant)
        .scalar_subquery()
    )
    active_tickets_subq = (
        select(func.count(Ticket.id))
        .join(Giveaway, Ticket.giveaway_id == Giveaway.id)
        .where(Ticket.participant_id == Participant.id, Giveaway.is_locked.is_(False))
        .correlate(Participant)
        .scalar_subquery()
    )

    stmt = select(
        Participant,
        total_tickets_subq.label("total_tickets"),
        active_tickets_subq.label("active_tickets"),
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Participant.phone.like(like), Participant.full_name.like(like)))
    if phone_verified is not None:
        stmt = stmt.where(Participant.phone_verified == phone_verified)
    if is_blocked is not None:
        stmt = stmt.where(Participant.is_blocked == is_blocked)
    if channel is not None:
        # .any(), не JOIN — участник может быть привязан к нескольким каналам,
        # JOIN размножил бы строки участника (сломав total/пагинацию).
        stmt = stmt.where(Participant.channel_bindings.any(ChannelBinding.channel == channel))
    if created_from is not None:
        stmt = stmt.where(Participant.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Participant.created_at < created_to + dt.timedelta(days=1))
    if total_tickets_min is not None:
        stmt = stmt.where(total_tickets_subq >= total_tickets_min)
    if total_tickets_max is not None:
        stmt = stmt.where(total_tickets_subq <= total_tickets_max)
    if active_tickets_min is not None:
        stmt = stmt.where(active_tickets_subq >= active_tickets_min)
    if active_tickets_max is not None:
        stmt = stmt.where(active_tickets_subq <= active_tickets_max)

    if export is not None:
        # Экспортируем все строки, подходящие под фильтр, а не только текущую
        # страницу — см. запрос пользователя: «именно те данные, которые
        # установлены в фильтре поиска».
        eager_stmt = stmt.options(selectinload(Participant.channel_bindings)).order_by(
            Participant.id.desc()
        )
        rows = session.execute(eager_stmt).all()
        tickets_map = _tickets_by_participant(session, [p.id for p, _, _ in rows])
        export_items = []
        for participant, total_tickets, active_tickets in rows:
            data = ParticipantOut.model_validate(participant).model_dump(mode="json")
            data["total_tickets"] = total_tickets
            data["active_tickets"] = active_tickets
            ticket_info = tickets_map.get(participant.id, {"tickets": "", "giveaways": ""})
            data["giveaways"] = ticket_info["giveaways"]
            data["tickets"] = ticket_info["tickets"]
            export_items.append(data)
        return maybe_export(
            export_items, export, user, "participants", permission=Permission.PARTICIPANTS_EXPORT
        )

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    page_stmt = (
        stmt.options(selectinload(Participant.channel_bindings))
        .order_by(Participant.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(page_stmt).all()
    items = []
    for participant, total_tickets, active_tickets in rows:
        data = ParticipantOut.model_validate(participant).model_dump(mode="json")
        data["total_tickets"] = total_tickets
        data["active_tickets"] = active_tickets
        items.append(data)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/by-phone", response_model=ParticipantOut | None)
def find_participant_by_phone(
    phone: str,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> Participant | None:
    """Поиск участника по номеру для автоподстановки имени в форме ручной
    регистрации (п.14.2/14.3 ТЗ — см. DECISIONS.md). Должен быть объявлен ДО
    `/{participant_id}`, иначе FastAPI попытается разобрать "by-phone" как id."""
    try:
        return participant_service.find_by_phone(session, phone)
    except InvalidPhoneError:
        return None


@router.get("/{participant_id}", response_model=ParticipantOut)
def get_participant(
    participant_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_PARTICIPANTS)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    return participant


@router.patch("/{participant_id}", response_model=ParticipantOut)
def update_participant(
    participant_id: int,
    payload: ParticipantUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PARTICIPANT_EDIT)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    if payload.full_name is not None:
        participant.full_name = payload.full_name
    details: dict[str, Any] = {}
    if payload.phone is not None:
        old_phone = participant.phone
        try:
            participant_service.change_phone(
                session, participant_id=participant.id, new_phone_raw=payload.phone
            )
        except InvalidPhoneError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный номер телефона"
            ) from exc
        except participant_service.PhoneAlreadyTakenError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if participant.phone != old_phone:
            details = {"old_phone": old_phone, "new_phone": participant.phone}
    session.flush()
    audit_service.log(
        session,
        action="participant_edit",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="participant",
        entity_id=participant.id,
        details=details or None,
        ip_address=request.client.host if request.client else None,
    )
    return participant


@router.post("/{participant_id}/block", response_model=ParticipantOut)
def block_participant(
    participant_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PARTICIPANT_BLOCK)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    participant.is_blocked = True
    session.flush()
    audit_service.log(
        session,
        action="participant_block",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="participant",
        entity_id=participant.id,
        ip_address=request.client.host if request.client else None,
    )
    return participant


@router.post("/{participant_id}/unblock", response_model=ParticipantOut)
def unblock_participant(
    participant_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.PARTICIPANT_BLOCK)),
) -> Participant:
    participant = session.get(Participant, participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден")
    participant.is_blocked = False
    session.flush()
    audit_service.log(
        session,
        action="participant_unblock",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="participant",
        entity_id=participant.id,
        ip_address=request.client.host if request.client else None,
    )
    return participant
