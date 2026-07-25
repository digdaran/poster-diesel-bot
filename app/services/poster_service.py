"""Сохранение/удаление цифровых постеров розыгрыша, загружаемых через
веб-админку (см. DECISIONS.md №46). Зеркалит `app/services/receipt_service.py` —
тот же паттерн (генерация имени файла, запись на диск, строка в БД), но
вызывается из HTTP-слоя (`backend/api/giveaways.py`), а не напрямую из бота,
поэтому аудит здесь не пишет сам — это делает вызывающий эндпоинт, как и для
остальных мутаций розыгрыша.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.giveaway_poster import GiveawayPoster

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_CONTENT_BYTES = 10 * 1024 * 1024


def save_poster(
    session: Session,
    settings: Settings,
    *,
    giveaway_id: int,
    content: bytes,
    content_type: str | None,
    original_filename: str | None,
) -> GiveawayPoster:
    """Вызывается из `backend/api/giveaways.py` в рамках уже открытой
    request-сессии (в отличие от `receipt_service.save_receipt`, которую бот
    вызывает напрямую без HTTP-слоя над ней и своей сессией)."""
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"Недопустимый тип файла: {content_type!r}. Разрешены: "
            f"{', '.join(sorted(_ALLOWED_CONTENT_TYPES))}"
        )
    if len(content) > _MAX_CONTENT_BYTES:
        raise ValueError(f"Файл превышает лимит {_MAX_CONTENT_BYTES // (1024 * 1024)} МБ")

    giveaway_dir = settings.poster_path / str(giveaway_id)
    giveaway_dir.mkdir(parents=True, exist_ok=True)
    extension = _ALLOWED_CONTENT_TYPES[content_type]
    file_path = giveaway_dir / f"{uuid.uuid4().hex}{extension}"
    file_path.write_bytes(content)

    poster = GiveawayPoster(
        giveaway_id=giveaway_id,
        file_path=str(file_path),
        original_filename=original_filename,
        content_type=content_type,
    )
    session.add(poster)
    session.flush()
    return poster


def delete_poster(*, poster: GiveawayPoster) -> None:
    Path(poster.file_path).unlink(missing_ok=True)
