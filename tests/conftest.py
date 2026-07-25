"""Общие фикстуры pytest: изолированная временная SQLite БД без Docker и сети.

Технический нюанс (см. DECISIONS.md): чистая `sqlite:///:memory:` не подходит для
тестов конкурентного захвата пула номеров (п.20.1 ТЗ) — SQLAlchemy использует
`SingletonThreadPool` для in-memory БД, и каждый поток/соединение получает
СВОЮ пустую базу. Поэтому фикстуры используют временный файл на диске
(`tempfile`, удаляется после теста) — это по-прежнему изолированно, без Docker
и без сети, но корректно эмулирует конкурентных писателей нескольких процессов.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models import Base
from sqlalchemy.orm import Session


def _remove_with_retry(path: str, attempts: int = 5, delay_s: float = 0.1) -> None:
    """На Windows файловый хендл может освобождаться ОС не мгновенно после
    `engine.dispose()` — короткий ретрай устраняет эту гонку без маскировки
    реальных ошибок (после `attempts` неудач исключение всё равно всплывает)."""
    for attempt in range(attempts):
        try:
            os.remove(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


@pytest.fixture
def tmp_db_path() -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # пусть SQLite создаст файл сам
    yield path
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = path + suffix
        if os.path.exists(p):
            _remove_with_retry(p)


@pytest.fixture
def settings(tmp_db_path: str) -> Settings:
    return Settings(
        JWT_SECRET="test-secret-not-for-production-use-only-in-tests",
        DATABASE_PATH=tmp_db_path,
        SUPERADMIN_PASSWORD="test-password",
    )


@pytest.fixture
def db(settings: Settings, tmp_db_path: str) -> Iterator[Database]:
    database = Database(settings, database_url=f"sqlite:///{tmp_db_path}")
    Base.metadata.create_all(database.engine)
    yield database
    # На Windows пул соединений держит файловый хендл открытым до dispose() —
    # без этого teardown tmp_db_path падает с PermissionError при удалении файла.
    database.engine.dispose()


@pytest.fixture
def session(db: Database) -> Iterator[Session]:
    with db.session() as s:
        yield s
