"""Funciones de acceso a la base de datos (lista_compra e historico_precios).

Cada función abre su propia conexión a DB_PATH (creando el fichero y las
tablas si es la primera vez). _connect() garantiza a la salida del `with`
las dos cosas que sqlite3 separa: commit/rollback de la transacción y
cierre determinista de la conexión (no dependemos del recolector de basura).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from initdb import DB_PATH, init_db


def _now() -> str:
    """Timestamp ISO 8601 en UTC. Formato fijo -> ordenar como texto es
    ordenar cronológicamente, clave para query_cheapest."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Conexión con transacción y cierre determinista.

    Ojo con sqlite3: `with sqlite3.connect(...) as conn` solo hace
    commit/rollback (NO cierra), y conn.close() solo cierra (NO confirma
    transacciones pendientes). Aquí se combinan los dos: el `with conn`
    interno gestiona la transacción y el `finally` cierra siempre, salga
    bien o salte una excepción.
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
    """Inserta el ítem; si ya existe la pareja (producto, supermercado),
    actualiza su precio y fecha.

    El precio se guarda SOLO en lista_compra: no se registra en
    historico_precios. Si al implementar la tool del Agent (Fase 2) se
    quiere que "añadir con precio" cuente como observación de precio,
    hay que llamar también a update_price — decisión pendiente.
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
    """Borra el ítem de la lista. Devuelve True si existía, False si no."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM lista_compra WHERE producto = ? AND supermercado = ?",
            (producto, supermercado),
        )
        return cur.rowcount > 0


def update_price(producto: str, supermercado: str, precio: float) -> None:
    """Actualiza ultimo_precio en lista_compra y añade una fila en
    historico_precios, ambas en la misma transacción."""
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
    """Precio más reciente de cada supermercado para ese producto, ordenado
    de más barato a más caro. Se apoya en que fecha es ISO 8601 con formato
    fijo, así que MAX(fecha) por supermercado es el último precio visto."""
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
    """Todo el contenido actual de lista_compra."""
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
