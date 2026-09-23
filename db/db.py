"""Database access functions (lista_compra and historico_precios).

Each function opens its own connection to DB_PATH (creating the file and
tables the first time). _connect() guarantees, on exiting the `with`, the two
things sqlite3 keeps separate: transaction commit/rollback and deterministic
connection close (not relying on the garbage collector).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from initdb import DB_PATH, init_db


def _now() -> str:
    """ISO 8601 UTC timestamp. Fixed format -> sorting as text sorts
    chronologically, which query_cheapest relies on."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Connection with transaction handling and deterministic close.

    Watch out with sqlite3: `with sqlite3.connect(...) as conn` only does
    commit/rollback (does NOT close), and conn.close() only closes (does NOT
    commit pending transactions). Here both are combined: the inner `with
    conn` manages the transaction and `finally` always closes, whether it
    succeeds or an exception is raised.
    """
    if not DB_PATH.exists():
        init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def add_item(producto: str, supermercado: str, precio: float | None) -> None:
    """Inserts the item; if the (producto, supermercado) pair already
    exists, updates its price and date instead.

    The price is stored ONLY in lista_compra: it is not logged to
    historico_precios. If an observed price needs to land in price history,
    use update_price instead (see mcp/server.py).
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO lista_compra (producto, supermercado, ultimo_precio, actualizado_en)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (producto, supermercado) DO UPDATE SET
                ultimo_precio = excluded.ultimo_precio,
                actualizado_en = excluded.actualizado_en
            """,
            (producto, supermercado, precio, _now()),
        )


def remove_item(producto: str, supermercado: str) -> bool:
    """Deletes the item from the list. Returns True if it existed, False otherwise."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM lista_compra WHERE producto = ? AND supermercado = ?",
            (producto, supermercado),
        )
        return cur.rowcount > 0


def update_price(producto: str, supermercado: str, precio: float) -> None:
    """Updates ultimo_precio in lista_compra and appends a row to
    historico_precios, both in the same transaction."""
    ahora = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE lista_compra
            SET ultimo_precio = ?, actualizado_en = ?
            WHERE producto = ? AND supermercado = ?
            """,
            (precio, ahora, producto, supermercado),
        )
        conn.execute(
            """
            INSERT INTO historico_precios (producto, supermercado, precio, fecha)
            VALUES (?, ?, ?, ?)
            """,
            (producto, supermercado, precio, ahora),
        )


def query_cheapest(producto: str) -> list[tuple[str, float]]:
    """Most recent price at each supermarket for that product, sorted
    cheapest to most expensive. Relies on `fecha` being ISO 8601 in a fixed
    format, so MAX(fecha) per supermarket is the latest observed price."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT supermercado, precio
            FROM (
                SELECT supermercado, precio,
                       ROW_NUMBER() OVER (
                           PARTITION BY supermercado
                           ORDER BY fecha DESC, id DESC
                       ) AS rn
                FROM historico_precios
                WHERE producto = ?
            )
            WHERE rn = 1
            ORDER BY precio ASC
            """,
            (producto,),
        )
        return [(supermercado, precio) for supermercado, precio in rows]


def list_current() -> list[dict]:
    """Everything currently in lista_compra."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, producto, supermercado, ultimo_precio, actualizado_en
            FROM lista_compra
            ORDER BY producto, supermercado
            """
        )
        return [dict(row) for row in rows]
