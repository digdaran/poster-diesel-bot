"""Раздел «Розыгрыши» (п.7.2, 11.4, 14.2 ТЗ)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.permissions import Permission
from app.models.base import utcnow
from app.models.enums import AuditActorType, ManualRegistrationStatus, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.giveaway_poster import GiveawayPoster
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.payment import Payment
from app.services import audit_service, poster_service
from app.services import ticket_pool_service as pool_svc
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, get_settings_dep, require_permission
from backend.api.schemas import (
    GiveawayCreateRequest,
    GiveawayOut,
    GiveawayPosterOut,
    GiveawayUpdateRequest,
)

router = APIRouter(prefix="/giveaways", tags=["giveaways"])


@router.get("", response_model=list[GiveawayOut])
def list_giveaways(
    q: str | None = None,
    is_registration_open: bool | None = None,
    is_locked: bool | None = None,
    is_archived: bool | None = None,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_GIVEAWAYS)),
) -> list[Giveaway]:
    # Без пагинации/конверта items+total намеренно: этот же список используется
    # как источник опций в select-фильтрах на других страницах (Продажи
    # On-Line/Номера/Ручные регистрации/Отчёты), которые дергают его без
    # параметров и ждут плоский массив — смена формы ответа сломала бы их все.
    # `is_archived` не задан по умолчанию (как и остальные фильтры) — эти
    # страницы по-прежнему видят архивные коллекции в своих фильтрах (нужно для
    # поиска исторических номерков/платежей); скрывает архивные только раздел
    # «Коллекции» (`is_archived=false`) и показывает раздел «Архив»
    # (`is_archived=true`) — см. GiveawaysPage.tsx/ArchivePage.tsx.
    stmt = select(Giveaway)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Giveaway.name.like(like), Giveaway.prefix.like(like)))
    if is_registration_open is not None:
        stmt = stmt.where(Giveaway.is_registration_open == is_registration_open)
    if is_locked is not None:
        stmt = stmt.where(Giveaway.is_locked == is_locked)
    if is_archived is not None:
        stmt = stmt.where(Giveaway.is_archived == is_archived)
    stmt = stmt.order_by(Giveaway.id.desc())
    return list(session.execute(stmt).scalars())


@router.get("/{giveaway_id}", response_model=GiveawayOut)
def get_giveaway(
    giveaway_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_GIVEAWAYS)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    return giveaway


@router.post("", response_model=GiveawayOut, status_code=status.HTTP_201_CREATED)
def create_giveaway(
    payload: GiveawayCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    giveaway = Giveaway(
        name=payload.name,
        prefix=payload.prefix,
        ticket_price=payload.ticket_price,
        max_tickets=payload.max_tickets,
        digital_poster_caption=payload.digital_poster_caption,
    )
    session.add(giveaway)
    session.flush()
    audit_service.log(
        session,
        action="giveaway_create",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.patch("/{giveaway_id}", response_model=GiveawayOut)
def update_giveaway(
    giveaway_id: int,
    payload: GiveawayUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    """prefix/ticket_price/max_tickets неизменяемы после opened_at (п.7.2 ТЗ) —
    их нет в GiveawayUpdateRequest, поэтому такие правки в принципе невозможны."""
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    if payload.name is not None:
        giveaway.name = payload.name
    if payload.digital_poster_caption is not None:
        giveaway.digital_poster_caption = payload.digital_poster_caption
    session.flush()
    audit_service.log(
        session,
        action="giveaway_update",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/open", response_model=GiveawayOut)
def open_registration(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    try:
        pool_svc.open_registration(session, giveaway)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.log(
        session,
        action="giveaway_open_registration",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/lock", response_model=GiveawayOut)
def lock_giveaway(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_LOCK)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_locked = True
    session.flush()
    audit_service.log(
        session,
        action="giveaway_lock",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/unlock", response_model=GiveawayOut)
def unlock_giveaway(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_LOCK)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_locked = False
    session.flush()
    audit_service.log(
        session,
        action="giveaway_unlock",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/close-registration", response_model=GiveawayOut)
def close_registration(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_registration_open = False
    session.flush()
    audit_service.log(
        session,
        action="giveaway_close_registration",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/archive", response_model=GiveawayOut)
def archive_giveaway(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_ARCHIVE)),
) -> Giveaway:
    """Архивация — не удаление (см. DECISIONS.md/DECISIONS_LOG.md): скрывает
    коллекцию из раздела «Коллекции» в «Архив», ни одна связанная запись
    (Ticket/Payment/ManualRegistration/аудит) не трогается и не теряется.
    Разрешена только когда регистрация закрыта навсегда и нет ни одной
    незавершённой (PENDING) заявки — иначе можно было бы спрятать коллекцию с
    ещё живыми, ожидающими решения заказами."""
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    if giveaway.is_registration_open:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нельзя архивировать: регистрация ещё не закрыта навсегда",
        )
    pending_payments = session.execute(
        select(func.count())
        .select_from(Payment)
        .where(Payment.giveaway_id == giveaway_id, Payment.status == PaymentStatus.PENDING)
    ).scalar_one()
    pending_registrations = session.execute(
        select(func.count())
        .select_from(ManualRegistration)
        .where(
            ManualRegistration.giveaway_id == giveaway_id,
            ManualRegistration.status == ManualRegistrationStatus.PENDING,
        )
    ).scalar_one()
    pending_total = pending_payments + pending_registrations
    if pending_total:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Нельзя архивировать: есть незавершённые заявки ({pending_total})",
        )
    giveaway.is_archived = True
    giveaway.archived_at = utcnow()
    session.flush()
    audit_service.log(
        session,
        action="giveaway_archive",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.post("/{giveaway_id}/unarchive", response_model=GiveawayOut)
def unarchive_giveaway(
    giveaway_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_ARCHIVE)),
) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    giveaway.is_archived = False
    giveaway.archived_at = None
    session.flush()
    audit_service.log(
        session,
        action="giveaway_unarchive",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        ip_address=request.client.host if request.client else None,
    )
    return giveaway


@router.get("/{giveaway_id}/posters", response_model=list[GiveawayPosterOut])
def list_giveaway_posters(
    giveaway_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_GIVEAWAYS)),
) -> list[GiveawayPoster]:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    return list(
        session.execute(
            select(GiveawayPoster)
            .where(GiveawayPoster.giveaway_id == giveaway_id)
            .order_by(GiveawayPoster.id)
        ).scalars()
    )


@router.post(
    "/{giveaway_id}/posters", response_model=GiveawayPosterOut, status_code=status.HTTP_201_CREATED
)
def upload_giveaway_poster(
    giveaway_id: int,
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> GiveawayPoster:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Розыгрыш не найден")
    try:
        poster = poster_service.save_poster(
            session,
            settings,
            giveaway_id=giveaway_id,
            content=file.file.read(),
            content_type=file.content_type,
            original_filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.log(
        session,
        action="giveaway_poster_upload",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway.id,
        details={"poster_id": poster.id, "content_type": poster.content_type},
        ip_address=request.client.host if request.client else None,
    )
    return poster


@router.delete(
    "/{giveaway_id}/posters/{poster_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_giveaway_poster(
    giveaway_id: int,
    poster_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.GIVEAWAY_EDIT)),
) -> None:
    poster = session.execute(
        select(GiveawayPoster).where(
            GiveawayPoster.id == poster_id, GiveawayPoster.giveaway_id == giveaway_id
        )
    ).scalar_one_or_none()
    if poster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Постер не найден")
    poster_service.delete_poster(poster=poster)
    session.delete(poster)
    session.flush()
    audit_service.log(
        session,
        action="giveaway_poster_delete",
        actor_type=AuditActorType.PANEL_USER,
        actor_id=user.id,
        actor_label=user.login,
        entity_type="giveaway",
        entity_id=giveaway_id,
        details={"poster_id": poster_id},
        ip_address=request.client.host if request.client else None,
    )


@router.get("/{giveaway_id}/posters/{poster_id}/file")
def download_giveaway_poster(
    giveaway_id: int,
    poster_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_GIVEAWAYS)),
) -> FileResponse:
    poster = session.execute(
        select(GiveawayPoster).where(
            GiveawayPoster.id == poster_id, GiveawayPoster.giveaway_id == giveaway_id
        )
    ).scalar_one_or_none()
    if poster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Постер не найден")
    return FileResponse(
        poster.file_path,
        media_type=poster.content_type or "application/octet-stream",
        filename=poster.original_filename or f"poster_{poster.id}",
    )
