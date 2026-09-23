"""Crea (o inicializa) la base de datos SQLite del proyecto.

Abre el fichero .sqlite con sqlite3.connect (lo crea si no existe) y ejecuta
schema.sql con executescript. Gracias a CREATE TABLE IF NOT EXISTS se puede
lanzar varias veces sin error.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "shopping_list.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def init_db(db_path: Path = DB_PATH) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema)


if __name__ == "__main__":
    init_db()
    print(f"Base de datos lista: {DB_PATH}")
