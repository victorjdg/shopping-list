"""
Servidor MCP de la lista de la compra: expone las 5 herramientas CRUD/E de db/db.py
(add_item, remove_item, update_price, query_cheapest, list_current) para que un
modelo (Mistral AI, conectado como "Connector" remoto vía HTTP) pueda consultar y
modificar la lista de la compra y el histórico de precios por lenguaje natural.

Mismo patrón de punta a punta que podman/mcp-server (Server): FastMCP del SDK
oficial `mcp` 1.x con transporte Streamable HTTP montado en "/mcp", escuchando en
0.0.0.0:8000 dentro del contenedor, y autenticación por middleware ASGI que exige
"Authorization: Bearer <token>" (MCP_AUTH_TOKEN) en cada petición, rechazando con
401 antes de llegar al manejador MCP.

La base de datos SQLite vive en el volumen montado en /appdata (ver 01-deploy.sh);
la ruta exacta se pasa en SHOPPING_LIST_DB. db/ no se toca: se importa tal cual y
se le parchea db.DB_PATH, inicializando el schema con initdb.init_db(ruta) de
forma explícita (ojo: el argumento por defecto de init_db apunta a la ruta que
db/ usa en local, no a la del contenedor -- por eso se pasa siempre explícitamente).

Nota sobre versión del SDK: mcp 2.x renombró la clase FastMCP a MCPServer y cambió
otras APIs; esta app usa FastMCP tal cual existe en la serie 1.x, así que
requirements.txt fija mcp<2.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

# db/ vive un nivel por encima de mcp/ (../db desde este fichero), igual que en
# local. En el contenedor: /app/mcp/server.py -> /app/db.
DB_DIR = Path(__file__).resolve().parent.parent / "db"
sys.path.insert(0, str(DB_DIR))

import db  # noqa: E402
import initdb  # noqa: E402

# ---------------------------------------------------------------------------
# Configuración y validación de entorno
# ---------------------------------------------------------------------------

try:
    MCP_AUTH_TOKEN: str = os.environ["MCP_AUTH_TOKEN"]
except KeyError as exc:
    sys.stderr.write(
        "ERROR FATAL: la variable de entorno MCP_AUTH_TOKEN es obligatoria y no está "
        "definida. Define MCP_AUTH_TOKEN con un token secreto antes de arrancar el "
        "servidor (por ejemplo: -e MCP_AUTH_TOKEN=... en el contenedor).\n"
    )
    raise RuntimeError(
        "Falta la variable de entorno obligatoria MCP_AUTH_TOKEN."
    ) from exc

if not MCP_AUTH_TOKEN.strip():
    raise RuntimeError(
        "La variable de entorno MCP_AUTH_TOKEN está definida pero vacía. "
        "Debe contener un token secreto no vacío."
    )

# Ruta del fichero SQLite. En el contenedor apunta al volumen /appdata (ver
# 01-deploy.sh); si no se define, cae a la que db/ usa por defecto (útil para
# pruebas en local sin contenedor).
SHOPPING_LIST_DB = Path(os.environ.get("SHOPPING_LIST_DB", str(db.DB_PATH)))

# Inicializa (de forma idempotente, CREATE TABLE IF NOT EXISTS) el schema en la
# ruta definitiva ANTES de parchear db.DB_PATH: el fallback de db._connect()
# llamaría a init_db() sin argumento, que crearía el fichero en la ruta por
# defecto de db/, no en la del volumen.
initdb.init_db(SHOPPING_LIST_DB)
db.DB_PATH = SHOPPING_LIST_DB

# ---------------------------------------------------------------------------
# Servidor MCP
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="shopping-list",
    instructions=(
        "Lista de la compra compartida con histórico de precios por supermercado. "
        "Permite añadir y quitar productos de la lista, registrar precios "
        "observados y consultar dónde está más barato un producto ahora mismo "
        "según el último precio conocido de cada supermercado."
    ),
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
)


@mcp.tool()
def add_item(producto: str, supermercado: str, precio: float | None = None) -> str:
    """Añade un producto a la lista de la compra.

    Si el producto ya estaba en la lista para ese supermercado, no se duplica:
    se actualiza su último precio y su fecha. El precio es opcional -- si el
    usuario no lo dice, se añade sin precio y se rellenará cuando se observe un
    precio real (por chat o procesando un ticket).

    Ojo: el precio que se pasa aquí se guarda solo como último precio de la
    lista, NO queda registrado en el histórico de precios. Si el usuario da un
    precio que acaba de ver ("he visto la leche a 1,20 en el Mercadona"),
    usa update_price en su lugar para que quede en el histórico.

    Útil ante peticiones como "añade leche del Mercadona a la lista" o
    "pon el pan de Lidl, cuesta 0,90".

    Args:
        producto: nombre del producto en el formato en que lo dice el usuario
            (p. ej. "leche entera"). La identidad del ítem es la pareja
            (producto, supermercado).
        supermercado: nombre del supermercado (p. ej. "Mercadona", "Lidl"). Si
            el usuario no especifica uno, pregúntaselo antes de llamar.
        precio: precio en euros observado para ese producto en ese
            supermercado, si se conoce. Opcional.
    """
    try:
        db.add_item(producto, supermercado, precio)
    except Exception as exc:  # noqa: BLE001 - mensaje legible, no stacktrace
        return f"Error al añadir '{producto}' ({supermercado}): {exc}"

    if precio is None:
        return f"Añadido a la lista: {producto} ({supermercado}), sin precio todavía."
    return (
        f"Añadido a la lista: {producto} ({supermercado}) a {precio:.2f} € "
        "(como último precio de la lista; no se ha registrado en el histórico)."
    )


@mcp.tool()
def remove_item(producto: str, supermercado: str) -> str:
    """Quita un producto de la lista de la compra.

    Borra solo el ítem (producto, supermercado) pedido. El histórico de precios
    NO se toca: los precios observados de ese producto se conservan, así que
    seguirá respondiendo a query_cheapest aunque ya no esté en la lista.

    Útil ante peticiones como "quita la leche del Mercadona" o "ya no quiero el
    pan de Lidl".

    Args:
        producto: nombre del producto tal y como está en la lista (usa
            list_current si no estás seguro de cómo está guardado).
        supermercado: supermercado del ítem a quitar.
    """
    try:
        existed = db.remove_item(producto, supermercado)
    except Exception as exc:  # noqa: BLE001
        return f"Error al quitar '{producto}' ({supermercado}): {exc}"

    if existed:
        return f"Quitado de la lista: {producto} ({supermercado})."
    return f"No estaba en la lista: {producto} ({supermercado})."


@mcp.tool()
def update_price(producto: str, supermercado: str, precio: float) -> str:
    """Registra un precio observado de un producto en un supermercado.

    Hace dos cosas en una sola transacción: actualiza el último precio del ítem
    en la lista de la compra (si está en ella) y añade una fila al histórico de
    precios con fecha y hora. Es la herramienta correcta cuando el usuario dice
    que ha visto un precio, tanto si el producto está en la lista como si no
    (el histórico guarda precios de productos aunque no estén en la lista).

    Útil ante peticiones como "la leche en el Lidl está ahora a 1,10" o "he
    pagado el pan 0,95 en el Mercadona".

    Args:
        producto: nombre del producto (p. ej. "leche entera").
        supermercado: supermercado donde se ha visto el precio.
        precio: precio en euros observado.
    """
    try:
        db.update_price(producto, supermercado, precio)
    except Exception as exc:  # noqa: BLE001
        return f"Error al registrar el precio de '{producto}' ({supermercado}): {exc}"

    return (
        f"Registrado: {producto} a {precio:.2f} € en {supermercado}. "
        "Actualizado el último precio de la lista y añadido al histórico."
    )


@mcp.tool()
def query_cheapest(producto: str) -> str:
    """Consulta dónde está más barato un producto AHORA, por supermercado.

    Devuelve el precio MÁS RECIENTE conocido de cada supermercado (según el
    histórico de precios), ordenado de más barato a más caro -- no el precio
    más bajo que tuvo nunca. Solo incluye supermercados de los que se ha
    registrado algún precio de ese producto en algún momento.

    Útil ante peticiones como "¿dónde está más barata la leche?" o "¿cuánto
    cuesta el pan en cada súper?".

    Args:
        producto: nombre del producto a consultar (p. ej. "leche entera"). Si no
            hay ningún precio registrado, se devuelve un mensaje indicándolo.
    """
    try:
        results = db.query_cheapest(producto)
    except Exception as exc:  # noqa: BLE001
        return f"Error al consultar los precios de '{producto}': {exc}"

    if not results:
        return (
            f"No hay ningún precio registrado de '{producto}' todavía. "
            "Regístralo con update_price cuando se observe uno."
        )

    lines = [f"Precios más recientes de {producto} (de más barato a más caro):"]
    for i, (supermercado, precio) in enumerate(results, start=1):
        lines.append(f"{i}. {supermercado}: {precio:.2f} €")
    return "\n".join(lines)


@mcp.tool()
def list_current() -> str:
    """Muestra todo el contenido actual de la lista de la compra.

    Devuelve cada ítem con su producto, supermercado, último precio conocido (si
    lo hay) y cuándo se actualizó por última vez. Útil ante peticiones como
    "¿qué hay en la lista?" o "muéstrame la lista de la compra", y también para
    comprobar cómo está guardado exactamente un producto antes de quitarlo o
    actualizar su precio.
    """
    try:
        items = db.list_current()
    except Exception as exc:  # noqa: BLE001
        return f"Error al leer la lista de la compra: {exc}"

    if not items:
        return "La lista de la compra está vacía."

    lines = ["Lista de la compra:"]
    for item in items:
        if item["ultimo_precio"] is None:
            lines.append(f"- {item['producto']} ({item['supermercado']}): sin precio todavía")
        else:
            lines.append(
                f"- {item['producto']} ({item['supermercado']}): "
                f"{item['ultimo_precio']:.2f} € (actualizado {item['actualizado_en']})"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Middleware ASGI de autenticación (Bearer token) y arranque del servidor
# ---------------------------------------------------------------------------


class BearerAuthMiddleware:
    """Middleware ASGI puro que exige `Authorization: Bearer <token>` en /mcp.

    Se aplica antes de despachar al manejador MCP: si la cabecera falta o no
    coincide con MCP_AUTH_TOKEN, responde 401 directamente sin invocar la app
    envuelta. Los eventos de tipo "lifespan" se dejan pasar sin comprobar nada
    (son inicio/parada del proceso, no peticiones HTTP), para que el
    StreamableHTTPSessionManager de FastMCP arranque y pare correctamente.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected_header = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")

        if not auth_header or not secrets.compare_digest(auth_header, self._expected_header):
            response = JSONResponse(
                {"error": "unauthorized", "detail": "Falta o es inválida la cabecera Authorization: Bearer <token>."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# App ASGI subyacente que expone FastMCP en Streamable HTTP, montada en "/mcp"
# (settings.mount_path="/" + settings.streamable_http_path="/mcp" -> path final "/mcp").
_streamable_http_app = mcp.streamable_http_app()

# App final: middleware de autenticación envolviendo la app MCP.
app = BearerAuthMiddleware(_streamable_http_app, MCP_AUTH_TOKEN)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
