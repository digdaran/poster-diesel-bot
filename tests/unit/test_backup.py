"""Тесты резервного копирования БД (п.18.1, 19 ТЗ): VACUUM INTO, сжатие, ротация."""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import time

from app.core.backup import run_backup


def test_backup_creates_valid_gzipped_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "source.db")
        backup_dir = os.path.join(tmp, "backups")

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('hello')")
        conn.commit()
        conn.close()

        result = run_backup(database_path=db_path, backup_dir=backup_dir, retention_days=14)

        assert result.exists()
        assert result.suffix == ".gz"

        restored_path = os.path.join(tmp, "restored.db")
        with gzip.open(result, "rb") as src, open(restored_path, "wb") as dst:
            dst.write(src.read())

        restored_conn = sqlite3.connect(restored_path)
        rows = restored_conn.execute("SELECT v FROM t").fetchall()
        restored_conn.close()
        assert rows == [("hello",)]


def test_backup_rotation_removes_old_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "source.db")
        backup_dir = os.path.join(tmp, "backups")
        os.makedirs(backup_dir)

        sqlite3.connect(db_path).close()

        old_file = os.path.join(backup_dir, "old-backup.db.gz")
        with open(old_file, "wb") as f:
            f.write(b"stale")
        old_time = time.time() - 30 * 86400  # 30 дней назад
        os.utime(old_file, (old_time, old_time))

        run_backup(database_path=db_path, backup_dir=backup_dir, retention_days=14)

        assert not os.path.exists(old_file)
