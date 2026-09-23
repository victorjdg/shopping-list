"""Backup diario del SQLite de la lista de la compra (Fase 5 de PLAN.md).

Usa la API `sqlite3.Connection.backup()` (no un `cp` a pelo) porque el fichero puede estar
abierto/en uso por el MCP server en el momento del backup -- `backup()` hace una copia
consistente de una base de datos SQLite en uso, un `cp` normal podría capturar un estado a
medio escribir. Pensado para lanzarse una vez al día vía cron (ver README.md de este
directorio), no dentro de un contenedor -- es un script suelto, sqlite3 ya es de la librería
estándar de Python (no hace falta ninguna dependencia nueva).

Uso: python3 backup.py <db_origen> <directorio_destino> [dias_retencion]
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
        print(f"Uso: {sys.argv[0]} <db_origen> <directorio_destino> [dias_retencion]", file=sys.stderr)
        sys.exit(1)

    db_path = Path(sys.argv[1])
    backup_dir = Path(sys.argv[2])
    retention_days = int(sys.argv[3]) if len(sys.argv) == 4 else DEFAULT_RETENTION_DAYS

    if not db_path.exists():
        print(f"Error: no existe la base de datos origen {db_path}", file=sys.stderr)
        sys.exit(1)

    dest = backup(db_path, backup_dir)
    size = dest.stat().st_size
    print(f"Backup creado: {dest} ({size} bytes)")

    removed = prune_old_backups(backup_dir, retention_days)
    if removed:
        print(f"Backups antiguos eliminados (>{retention_days} días): {len(removed)}")

    # Ocupación total del directorio de backups, para detectar crecimiento inesperado a simple
    # vista en el log de cron sin tener que entrar a mirar a mano.
    total = sum(f.stat().st_size for f in backup_dir.glob("shopping_list-*.sqlite"))
    print(f"Total en {backup_dir}: {shutil.disk_usage(backup_dir).used} usados en el disco, "
          f"{total} bytes en backups de la lista")


if __name__ == "__main__":
    main()
