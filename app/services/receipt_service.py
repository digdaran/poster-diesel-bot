"""Сохранение квитанций об оплате, присланных участниками (см. ТЗ, DECISIONS.md).

Квитанции НЕ распознаются — только сохраняются на диск (см. `Settings.receipts_dir`,
тот же bind-mount паттерн в `./data/`, что и для БД, см. DECISIONS_LOG.md №28) и
привязываются к `Payment` для просмотра оператором в панели (раздел «Продажи»).
Единая точка входа для обоих мессенджер-каналов (Telegram/VK) — оба делят один
пакет `app/` и одну БД, поэтому вызывают эту функцию напрямую, без HTTP.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import Settings
from app.core.db import Database
from app.models.enums import AuditActorType
from app.models.payment_receipt import PaymentReceipt
from app.services import audit_service


def save_receipt(
    db: Database,
    settings: Settings,
    *,
    payment_id: int,
    content: bytes,
    content_type: str | None,
    original_filename: str | None,
    telegram_file_id: str | None = None,
    vk_attachment: str | None = None,
) -> PaymentReceipt:
    payment_dir = settings.receipts_path / str(payment_id)
    payment_dir.mkdir(parents=True, exist_ok=True)

    extension = _guess_extension(content_type=content_type, original_filename=original_filename)
    file_name = f"{uuid.uuid4().hex}{extension}"
    file_path = payment_dir / file_name
    file_path.write_bytes(content)

    with db.session() as session:
        receipt = PaymentReceipt(
            payment_id=payment_id,
            file_path=str(file_path),
            original_filename=original_filename,
            content_type=content_type,
            telegram_file_id=telegram_file_id,
            vk_attachment=vk_attachment,
        )
        session.add(receipt)
        session.flush()
        audit_service.log(
            session,
            action="receipt_uploaded",
            actor_type=AuditActorType.PARTICIPANT,
            actor_label="bot",
            entity_type="payment",
            entity_id=payment_id,
            details={"receipt_id": receipt.id, "content_type": content_type},
        )
        # `expire_on_commit=False` (см. Database.session_factory) — объект
        # остаётся пригодным для чтения после выхода из `with` (session.commit()).
        return receipt


def _guess_extension(*, content_type: str | None, original_filename: str | None) -> str:
    if original_filename:
        suffix = Path(original_filename).suffix
        if suffix:
            return suffix
    if content_type:
        guessed = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
        }.get(content_type)
        if guessed:
            return guessed
    return ".bin"
