"""Раздел «Продажи» — список онлайн-платежей (п.11.4, 13, 14.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.permissions import Permission
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import PaymentOut, PaymentReceiptOut

router = APIRouter(prefix="/payments", tags=["sales"])


def _to_dict(p: Payment) -> dict[str, Any]:
    invoice_no = (
        p.giveaway.format_invoice_number(p.payment_number) if p.payment_number is not None else None
    )
    return PaymentOut(
        id=p.id,
        order_id=p.order_id,
        participant_id=p.participant_id,
        participant_phone=p.participant.phone,
        participant_full_name=p.participant.full_name,
        giveaway_id=p.giveaway_id,
        giveaway_name=p.giveaway.name,
        provider=p.provider.value,
        channel=p.channel.value if p.channel else None,
        amount=p.amount,
        quantity=p.quantity,
        status=p.status.value,
        created_at=p.created_at,
        confirmed_at=p.confirmed_at,
        invoice_no=invoice_no,
        oversold=p.oversold,
        amount_mismatch=p.amount_mismatch,
        amount_mismatch_bank_amount=p.amount_mismatch_bank_amount,
        receipt_count=len(p.receipts),
    ).model_dump(mode="json")


@router.get("", response_model=None)
def list_payments(
    giveaway_id: int | None = None,
    status_filter: PaymentStatus | None = None,
    order_id: str | None = None,
    invoice_no: str | None = None,
    provider: PaymentProviderType | None = None,
    channel: ChannelType | None = None,
    participant_id: int | None = None,
    participant_query: str | None = None,
    amount_mismatch: bool | None = None,
    oversold: bool | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    page: int = 1,
    page_size: int = 50,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> dict[str, Any] | list[dict[str, Any]] | Response:
    validate_page_size(page_size)
    stmt = select(Payment)
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    if status_filter is not None:
        stmt = stmt.where(Payment.status == status_filter)
    if order_id:
        stmt = stmt.where(Payment.order_id.like(f"%{order_id}%"))
    if provider is not None:
        stmt = stmt.where(Payment.provider == provider)
    if channel is not None:
        stmt = stmt.where(Payment.channel == channel)
    if participant_id is not None:
        stmt = stmt.where(Payment.participant_id == participant_id)
    if amount_mismatch is not None:
        stmt = stmt.where(Payment.amount_mismatch == amount_mismatch)
    if oversold is not None:
        stmt = stmt.where(Payment.oversold == oversold)
    if created_from is not None:
        stmt = stmt.where(Payment.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Payment.created_at < created_to + dt.timedelta(days=1))
    if participant_query:
        like = f"%{participant_query}%"
        stmt = stmt.join(Payment.participant).where(
            or_(Participant.phone.like(like), Participant.full_name.like(like))
        )
    if invoice_no:
        # invoice_no не хранится отдельной колонкой (см. Giveaway.format_invoice_number)
        # — собираем ту же строку "PREFIX-NNNNN" на стороне БД и матчим подстрокой,
        # это позволяет искать и по префиксу, и по номеру счёта, и по полному значению.
        like = f"%{invoice_no}%"
        stmt = stmt.join(Payment.giveaway).where(
            (Giveaway.prefix + "-" + func.printf("%05d", Payment.payment_number)).like(like)
        )
    # Уже сделали .join(...) выше ради фильтров — populate из тех же строк через
    # contains_eager, а не отдельным joinedload (иначе лишний JOIN).
    participant_load = (
        contains_eager(Payment.participant)
        if participant_query
        else joinedload(Payment.participant)
    )
    giveaway_load = contains_eager(Payment.giveaway) if invoice_no else joinedload(Payment.giveaway)

    eager_options = (participant_load, giveaway_load, joinedload(Payment.receipts))

    if export is not None:
        eager_stmt = stmt.options(*eager_options).order_by(Payment.id.desc())
        rows = [_to_dict(p) for p in session.execute(eager_stmt).unique().scalars()]
        return maybe_export(rows, export, user, "sales", permission=Permission.SALES_EXPORT)

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    eager_stmt = (
        stmt.options(*eager_options).order_by(Payment.id.desc()).limit(limit).offset(offset)
    )
    items = session.execute(eager_stmt).unique().scalars()
    return {
        "items": [_to_dict(p) for p in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{payment_id}/receipts", response_model=list[PaymentReceiptOut])
def list_payment_receipts(
    payment_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> list[PaymentReceiptOut]:
    receipts = list(
        session.execute(
            select(PaymentReceipt)
            .where(PaymentReceipt.payment_id == payment_id)
            .order_by(PaymentReceipt.id.desc())
        ).scalars()
    )
    return [PaymentReceiptOut.model_validate(r) for r in receipts]


@router.get("/{payment_id}/receipts/{receipt_id}/file")
def download_payment_receipt(
    payment_id: int,
    receipt_id: int,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> FileResponse:
    receipt = session.execute(
        select(PaymentReceipt).where(
            PaymentReceipt.id == receipt_id, PaymentReceipt.payment_id == payment_id
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Квитанция не найдена")
    return FileResponse(
        receipt.file_path,
        media_type=receipt.content_type or "application/octet-stream",
        filename=receipt.original_filename or f"receipt_{receipt.id}",
    )
