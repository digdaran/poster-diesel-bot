"""Резервное копирование SQLite: консистентный снимок через `VACUUM INTO`,
сжатие, ротация по сроку хранения (п.18.1, 19 ТЗ).

Запускается внутри backend-контейнера: `python -m app.core.backup`
(см. scripts/backup_db.sh).
"""

from __future__ import annotations

import datetime as dt
import gzip
import os
import shutil
import sqlite3
import sys
from pathlib import Path


def run_backup(*, database_path: str, backup_dir: str, retention_days: int) -> Path:
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = Path(backup_dir) / f"raffle-{timestamp}.db"

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(f"VACUUM INTO '{snapshot_path.as_posix()}'")
    finally:
        connection.close()

    gz_path = snapshot_path.with_suffix(snapshot_path.suffix + ".gz")
    with open(snapshot_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    snapshot_path.unlink()

    _rotate(backup_dir, retention_days)
    return gz_path


def _rotate(backup_dir: str, retention_days: int) -> None:
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - retention_days * 86400
    for entry in Path(backup_dir).iterdir():
        if entry.is_file() and entry.stat().st_mtime < cutoff:
            entry.unlink()


def main() -> None:
    database_path = os.environ.get("DATABASE_PATH", "/data/raffle.db")
    backup_dir = os.environ.get("BACKUP_DIR", "/backups")
    retention_days = int(os.environ.get("BACKUP_RETENTION_DAYS", "14"))

    result = run_backup(
        database_path=database_path, backup_dir=backup_dir, retention_days=retention_days
    )
    print(f"Backup written: {result}", file=sys.stderr)


if __name__ == "__main__":
    main()
