"""Daily backup of the shopping list's SQLite database.

Uses the `sqlite3.Connection.backup()` API (not a plain `cp`) because the file may be
open/in use by the MCP server at backup time -- `backup()` makes a consistent copy of a
SQLite database that's in use, whereas a plain `cp` could capture a mid-write,
inconsistent state. Meant to run once a day via cron (see this directory's README.md),
not inside a container -- it's a standalone script, sqlite3 is already part of Python's
standard library (no new dependency needed).

Usage: python3 backup.py <source_db> <backup_dir> [retention_days]
"""

import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_RETENTION_DAYS = 14


def backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = backup_dir / f"shopping_list-{timestamp}.sqlite"

    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    return dest


def prune_old_backups(backup_dir: Path, retention_days: int) -> list[Path]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = []
    for f in backup_dir.glob("shopping_list-*.sqlite"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            f.unlink()
            removed.append(f)
    return removed


def main() -> None:
    if len(sys.argv) not in (3, 4):
        print(f"Usage: {sys.argv[0]} <source_db> <backup_dir> [retention_days]", file=sys.stderr)
        sys.exit(1)

    db_path = Path(sys.argv[1])
    backup_dir = Path(sys.argv[2])
    retention_days = int(sys.argv[3]) if len(sys.argv) == 4 else DEFAULT_RETENTION_DAYS

    if not db_path.exists():
        print(f"Error: source database {db_path} doesn't exist", file=sys.stderr)
        sys.exit(1)

    dest = backup(db_path, backup_dir)
    size = dest.stat().st_size
    print(f"Backup created: {dest} ({size} bytes)")

    removed = prune_old_backups(backup_dir, retention_days)
    if removed:
        print(f"Old backups removed (>{retention_days} days): {len(removed)}")

    # Total size of the backup directory, to spot unexpected growth at a glance in the
    # cron log without having to go check by hand.
    total = sum(f.stat().st_size for f in backup_dir.glob("shopping_list-*.sqlite"))
    print(f"Total in {backup_dir}: {shutil.disk_usage(backup_dir).used} bytes used on disk, "
          f"{total} bytes in list backups")


if __name__ == "__main__":
    main()
