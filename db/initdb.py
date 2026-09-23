"""Creates (or initializes) the project's SQLite database.

Opens the .sqlite file with sqlite3.connect (creating it if it doesn't
exist) and runs schema.sql via executescript. Thanks to CREATE TABLE IF NOT
EXISTS, it can be run more than once without error.
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
    print(f"Database ready: {DB_PATH}")
