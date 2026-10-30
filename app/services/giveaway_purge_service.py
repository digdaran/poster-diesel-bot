"""Удаление чеков архивной коллекции с диска и полное удаление архивной коллекции
(DECISIONS_LOG.md №89). Только для заархивированных коллекций, право `GIVEAWAY_PURGE`
(Super Admin).

- «Удалить чеки» — файлы чеков удаляются с диска, записи `PaymentReceipt` остаются с
  пометкой `deleted_at` (в «Продажах» видно, что чек был, но удалён).
- «Удалить коллекцию» — удаляются коллекция, её пул номеров, номера, онлайн-счета (с чеками),
  ручные регистрации и постеры (вместе с файлами). Участники (общие для всех коллекций) и
  журнал аудита остаются; в аудит пишется запись с итогами удаления.

Сначала коммитится изменение в БД, файлы удаляются после: если удаление файла не удастся,
останется лишний файл на диске (не битая ссылка в БД). Такие сбои считаются в `file_errors`.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog
from sqlalchemy import ColumnElement, Select, delete, func, select, update
from sqlalchemy.orm import Session

from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import AuditActorType
from app.models.giveaway import Giveaway
from app.models.giveaway_poster import GiveawayPoster
from app.models.manual_registration import ManualRegistration
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
from app.models.ticket import Ticket
from app.models.ticket_pool import TicketPool
from app.services import audit_service

logger = structlog.get_logger(__name__)


class GiveawayNotFoundError(Exception):
    pass


class GiveawayNotArchivedError(Exception):
    pass


@dataclass(frozen=True)
class PurgeSummary:
    """Что будет (или было) удалено — для диалога подтверждения и записи аудита."""

    giveaway_name: str
    prefix: str
    receipts: int
    """Чеки, файлы которых ещё не удалены."""
    payments: int
    manual_registrations: int
    tickets: int
    posters: int


@dataclass(frozen=True)
class PurgeResult:
    summary: PurgeSummary
    file_errors: int


@dataclass(frozen=True)
class Actor:
    panel_user_id: int
    login: str
    ip_address: str | None = None


def _payment_ids(giveaway_id: int) -> Select[tuple[int]]:
    return select(Payment.id).where(Payment.giveaway_id == giveaway_id)


def _active_receipts_stmt(giveaway_id: int) -> Select[tuple[PaymentReceipt]]:
    return select(PaymentReceipt).where(
        PaymentReceipt.payment_id.in_(_payment_ids(giveaway_id)),
        PaymentReceipt.deleted_at.is_(None),
    )


def _count(session: Session, model: type, *where: ColumnElement[bool]) -> int:
    return int(session.execute(select(func.count()).select_from(model).where(*where)).scalar_one())


def _load_archived(session: Session, giveaway_id: int) -> Giveaway:
    giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        raise GiveawayNotFoundError(f"Коллекция {giveaway_id} не найдена")
    if not giveaway.is_archived:
        raise GiveawayNotArchivedError("Удалять можно только коллекции из архива")
    return giveaway


def get_summary(session: Session, giveaway_id: int) -> PurgeSummary:
    giveaway = _load_archived(session, giveaway_id)
    return PurgeSummary(
        giveaway_name=giveaway.name,
        prefix=giveaway.prefix,
        receipts=_count(
            session,
            PaymentReceipt,
            PaymentReceipt.payment_id.in_(_payment_ids(giveaway_id)),
            PaymentReceipt.deleted_at.is_(None),
        ),
        payments=_count(session, Payment, Payment.giveaway_id == giveaway_id),
        manual_registrations=_count(
            session, ManualRegistration, ManualRegistration.giveaway_id == giveaway_id
        ),
        tickets=_count(session, Ticket, Ticket.giveaway_id == giveaway_id),
        posters=_count(session, GiveawayPoster, GiveawayPoster.giveaway_id == giveaway_id),
    )


def delete_receipts(
    db: Database, *, giveaway_id: int, actor: Actor, now: dt.datetime | None = None
) -> PurgeResult:
    """Удаляет с диска файлы всех чеков архивной коллекции, записи помечает `deleted_at`."""
    now = now or utcnow()
    with db.immediate_session() as session:
        summary = get_summary(session, giveaway_id)
        receipts = list(session.execute(_active_receipts_stmt(giveaway_id)).scalars())
        paths = [r.file_path for r in receipts]
        if receipts:
            session.execute(
                update(PaymentReceipt)
                .where(PaymentReceipt.id.in_([r.id for r in receipts]))
                .values(deleted_at=now)
            )
        _audit(session, "giveaway_receipts_delete", giveaway_id, actor, summary)
    file_errors = _remove_files(paths)
    return PurgeResult(summary=summary, file_errors=file_errors)


def purge_giveaway(db: Database, *, giveaway_id: int, actor: Actor) -> PurgeResult:
    """Полностью удаляет архивную коллекцию со всеми её записями и файлами (чеки, постеры).
    Участники и журнал аудита не трогаются."""
    with db.immediate_session() as session:
        summary = get_summary(session, giveaway_id)
        paths = [r.file_path for r in session.execute(_active_receipts_stmt(giveaway_id)).scalars()]
        paths += list(
            session.execute(
                select(GiveawayPoster.file_path).where(GiveawayPoster.giveaway_id == giveaway_id)
            ).scalars()
        )
        _audit(session, "giveaway_purge", giveaway_id, actor, summary)

        payment_ids = _payment_ids(giveaway_id)
        # Порядок важен: внешние ключи включены (PRAGMA foreign_keys=ON).
        session.execute(delete(Ticket).where(Ticket.giveaway_id == giveaway_id))
        session.execute(delete(PaymentReceipt).where(PaymentReceipt.payment_id.in_(payment_ids)))
        session.execute(delete(TicketPool).where(TicketPool.giveaway_id == giveaway_id))
        session.execute(delete(Payment).where(Payment.giveaway_id == giveaway_id))
        session.execute(
            delete(ManualRegistration).where(ManualRegistration.giveaway_id == giveaway_id)
        )
        session.execute(delete(GiveawayPoster).where(GiveawayPoster.giveaway_id == giveaway_id))
        session.execute(delete(Giveaway).where(Giveaway.id == giveaway_id))
    file_errors = _remove_files(paths)
    return PurgeResult(summary=summary, file_errors=file_errors)


def _audit(
    session: Session, action: str, giveaway_id: int, actor: Actor, summary: PurgeSummary
) -> None:
    audit_service.log(
        session,
        action=action,
        actor_type=AuditActorType.PANEL_USER,
        actor_id=actor.panel_user_id,
        actor_label=actor.login,
        entity_type="giveaway",
        entity_id=giveaway_id,
        ip_address=actor.ip_address,
        details=asdict(summary),
    )


def _remove_files(paths: list[str]) -> int:
    """Удаляет файлы и опустевшие после этого каталоги (`receipts/<счёт>/`,
    `posters/<коллекция>/`). Уже отсутствующий файл — не ошибка."""
    errors = 0
    dirs: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        try:
            path.unlink(missing_ok=True)
            dirs.add(path.parent)
        except OSError:
            errors += 1
            logger.exception("purge_file_delete_failed", path=raw)
    for directory in dirs:
        with contextlib.suppress(OSError):  # каталог не пуст — оставляем
            directory.rmdir()
    return errors
