"""Инициализация SQLAlchemy engine для SQLite: WAL, busy_timeout, BEGIN IMMEDIATE.

Единая точка создания engine/сессий для всех процессов (backend, channel-telegram,
фоновые задачи). Реализует рекомендованный SQLAlchemy-паттерн для pysqlite:
отключаем автоматическое управление транзакциями драйвера (`isolation_level = None`)
и берём его на себя через событие `begin`, чтобы иметь возможность выдавать
`BEGIN IMMEDIATE` там, где это критично для согласованности (пул номеров,
финализация платежа) — см. ARCHITECTURE.md, п.3-4.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

_BEGIN_MODE_KEY = "raffle_begin_mode"


def create_engine_for(
    database_url: str, *, busy_timeout_ms: int = 5000, echo: bool = False
) -> Engine:
    """Создаёт SQLAlchemy Engine для SQLite с WAL + busy_timeout + управляемыми транзакциями.

    Для `sqlite:///:memory:` WAL недоступен (нет файла на диске) — pragma просто
    не даёт эффекта, но busy_timeout/foreign_keys всё равно выставляются.
    """
    from sqlalchemy import create_engine

    is_memory = ":memory:" in database_url
    engine = create_engine(
        database_url, echo=echo, connect_args={"check_same_thread": False}, future=True
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: object, _connection_record: object) -> None:  # noqa: ANN001
        # Отключаем автоматическое открытие транзакции драйвером pysqlite —
        # управляем BEGIN/BEGIN IMMEDIATE явно через событие "begin" ниже.
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        if not is_memory:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Connection) -> None:
        mode = conn.info.pop(_BEGIN_MODE_KEY, "deferred")
        if mode == "immediate":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.exec_driver_sql("BEGIN")

    return engine


class Database:
    """Обёртка над engine + sessionmaker, используемая сервисами и репозиториями."""

    def __init__(
        self, settings: Settings | None = None, *, database_url: str | None = None
    ) -> None:
        self.settings = settings or get_settings()
        url = database_url or self.settings.database_url
        self.engine = create_engine_for(url, busy_timeout_ms=self.settings.db_busy_timeout_ms)
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Обычная ORM-сессия с deferred BEGIN — для операций, не требующих
        немедленной writer-блокировки."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def immediate_session(self) -> Iterator[Session]:
        """ORM-сессия, транзакция которой открыта через `BEGIN IMMEDIATE`.

        Используется для атомарного захвата пула номеров (п.7.5 ТЗ) и идемпотентной
        финализации платежа (п.7.6 ТЗ): захватывает writer-lock немедленно, конкурирующие
        писатели других процессов встают в очередь на `busy_timeout`, а не падают с
        `SQLITE_BUSY`.
        """
        connection = self.engine.connect()
        connection.info[_BEGIN_MODE_KEY] = "immediate"
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            session.connection()  # триггерит autobegin -> событие "begin" -> BEGIN IMMEDIATE
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            connection.close()


def ping(session: Session) -> bool:
    return session.execute(text("SELECT 1")).scalar_one() == 1
