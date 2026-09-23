"""
Shopping list MCP server: exposes db/db.py's 5 CRUD/query tools (add_item, remove_item,
update_price, query_cheapest, list_current) so a model (Mistral AI, connected as a remote
"Connector" over HTTP) can query and modify the shopping list and price history via
natural language.

Same end-to-end pattern as any FastMCP-based server: official `mcp` 1.x SDK with
Streamable HTTP transport mounted at "/mcp", listening on 0.0.0.0:8000 inside the
container, and auth via an ASGI middleware that requires "Authorization: Bearer <token>"
(MCP_AUTH_TOKEN) on every request, rejecting with 401 before reaching the MCP handler.

The SQLite database lives on the volume mounted at /appdata (see 01-deploy.sh); the exact
path is passed via SHOPPING_LIST_DB. db/ is imported as-is, unmodified: db.DB_PATH gets
patched, and the schema is initialized explicitly via initdb.init_db(path) (note: the
default argument of init_db points at the path db/ uses locally, not the container's --
that's why it's always passed explicitly).

Note on SDK version: mcp 2.x renamed the FastMCP class to MCPServer and changed other
APIs; this app uses FastMCP as it exists in the 1.x series, so requirements.txt pins
mcp<2.
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

# Note: importing mistralai.workflows.client (used further down, in
# process_receipt_photo) prints a noisy INFO log with the full Workflows SDK
# configuration (it's built to potentially run a worker, even though it's only used
# here as a client) -- expected, not an error. Shows up once at container startup
# (module-level import), not on every tool call.
from mistralai.extra.workflows import WorkflowEncodingConfig, configure_workflow_encoding
from mistralai.workflows.client import get_mistral_client

# db/ lives one level above mcp/ (../db from this file), same as locally.
# Inside the container: /app/mcp/server.py -> /app/db.
DB_DIR = Path(__file__).resolve().parent.parent / "db"
sys.path.insert(0, str(DB_DIR))

import db  # noqa: E402
import initdb  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration and environment validation
# ---------------------------------------------------------------------------

try:
    MCP_AUTH_TOKEN: str = os.environ["MCP_AUTH_TOKEN"]
except KeyError as exc:
    sys.stderr.write(
        "FATAL ERROR: the MCP_AUTH_TOKEN environment variable is required and not "
        "set. Set MCP_AUTH_TOKEN to a secret token before starting the server (e.g. "
        "-e MCP_AUTH_TOKEN=... on the container).\n"
    )
    raise RuntimeError(
        "Missing required environment variable MCP_AUTH_TOKEN."
    ) from exc

if not MCP_AUTH_TOKEN.strip():
    raise RuntimeError(
        "The MCP_AUTH_TOKEN environment variable is set but empty. "
        "It must contain a non-empty secret token."
    )

# Credentials to trigger the "shopping-receipt" Workflow (Mistral Workflows) from the
# process_receipt_photo tool -- same early-validation pattern as MCP_AUTH_TOKEN.
# WORKFLOWS_DEPLOYMENT_NAME must match the deployment of the worker that registers that
# workflow (see workflow/README.md).
try:
    MISTRAL_API_KEY: str = os.environ["MISTRAL_API_KEY"]
except KeyError as exc:
    sys.stderr.write(
        "FATAL ERROR: the MISTRAL_API_KEY environment variable is required (needed "
        "to trigger the shopping-receipt Workflow from process_receipt_photo).\n"
    )
    raise RuntimeError("Missing required environment variable MISTRAL_API_KEY.") from exc

try:
    WORKFLOWS_DEPLOYMENT_NAME: str = os.environ["WORKFLOWS_DEPLOYMENT_NAME"]
except KeyError as exc:
    sys.stderr.write(
        "FATAL ERROR: the WORKFLOWS_DEPLOYMENT_NAME environment variable is required "
        "(deployment name of the worker that registers the shopping-receipt workflow).\n"
    )
    raise RuntimeError(
        "Missing required environment variable WORKFLOWS_DEPLOYMENT_NAME."
    ) from exc

WORKFLOWS_SERVER_URL = os.environ.get("WORKFLOWS_SERVER_URL", "https://api.mistral.ai")

# SQLite file path. Inside the container points at the /appdata volume (see
# 01-deploy.sh); if unset, falls back to whatever db/ uses by default (handy for local
# testing without a container).
SHOPPING_LIST_DB = Path(os.environ.get("SHOPPING_LIST_DB", str(db.DB_PATH)))

# Initializes (idempotently, CREATE TABLE IF NOT EXISTS) the schema at the final path
# BEFORE patching db.DB_PATH: db._connect()'s fallback would call init_db() with no
# argument, which would create the file at db/'s default path, not the volume's.
initdb.init_db(SHOPPING_LIST_DB)
db.DB_PATH = SHOPPING_LIST_DB

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="shopping-list",
    instructions=(
        "Shared shopping list with per-supermarket price history. Lets you add and "
        "remove products from the list, log observed prices, and check where a "
        "product is cheapest right now based on the latest known price at each "
        "supermarket.\n\n"
        "IMPORTANT about receipt photos: if the user attaches a photo of a receipt, do "
        "NOT read the prices from the image yourself or call update_price line by "
        "line -- even though you can see the photo, receipts with discounts or "
        "promotions need a calculation a model doesn't do reliably (verified in real "
        "tests: it gets the discount arithmetic wrong). ALWAYS call "
        "process_receipt_photo with the image in base64 and let that process do the "
        "math; never process the receipt yourself under any circumstances."
    ),
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
)


@mcp.tool()
def add_item(producto: str, supermercado: str, precio: float | None = None) -> str:
    """Adds a product to the shopping list.

    If the product is already on the list for that supermarket, it isn't duplicated:
    its last known price and date get updated instead. The price is optional -- if the
    user doesn't give one, it's added without a price, to be filled in later when a
    real price is observed (via chat or by processing a receipt).

    Note: the price passed here is stored only as the list's last known price, it is
    NOT logged to price history. If the user gives a price they just observed ("I saw
    milk at 1.20 at the supermarket"), use update_price instead so it gets logged.

    Useful for requests like "add milk from the supermarket to the list" or "add bread
    from the discount store, it costs 0.90".

    Args:
        producto: product name in whatever form the user says it (e.g. "whole milk").
            The item's identity is the (producto, supermercado) pair.
        supermercado: supermarket name (e.g. "Lidl", "Mercadona"). If the user doesn't
            specify one, ask before calling.
        precio: observed price in euros for that product at that supermarket, if
            known. Optional.
    """
    try:
        db.add_item(producto, supermercado, precio)
    except Exception as exc:  # noqa: BLE001 - readable message, not a stack trace
        return f"Error adding '{producto}' ({supermercado}): {exc}"

    if precio is None:
        return f"Added to the list: {producto} ({supermercado}), no price yet."
    return (
        f"Added to the list: {producto} ({supermercado}) at {precio:.2f} EUR "
        "(as the list's last known price; not logged to price history)."
    )


@mcp.tool()
def remove_item(producto: str, supermercado: str) -> str:
    """Removes a product from the shopping list.

    Only deletes the requested (producto, supermercado) item. Price history is NOT
    touched: observed prices for that product are kept, so it will still answer
    query_cheapest even after being removed from the list.

    Useful for requests like "remove milk from the supermarket" or "I don't need the
    bread from the discount store anymore".

    Args:
        producto: product name exactly as it's stored on the list (use list_current
            if you're not sure how it's stored).
        supermercado: supermarket of the item to remove.
    """
    try:
        existed = db.remove_item(producto, supermercado)
    except Exception as exc:  # noqa: BLE001
        return f"Error removing '{producto}' ({supermercado}): {exc}"

    if existed:
        return f"Removed from the list: {producto} ({supermercado})."
    return f"Wasn't on the list: {producto} ({supermercado})."


@mcp.tool()
def update_price(producto: str, supermercado: str, precio: float) -> str:
    """Logs an observed price for a product at a supermarket.

    Does two things in a single transaction: updates the item's last known price on
    the shopping list (if it's on it) and appends a row to price history with a
    timestamp. The right tool whenever the user says they've seen a price, whether or
    not the product is currently on the list (price history keeps prices for products
    even if they're not on the list).

    Useful for requests like "milk at the supermarket is now 1.10" or "I paid 0.95 for
    bread at the discount store".

    Do NOT use this tool to process a photo of a receipt, even if you can see the
    prices in the image -- use process_receipt_photo instead, which correctly
    calculates discounts and promotions (a model eyeballing a receipt gets that
    arithmetic wrong). This tool is only for a single price the user mentions in text.

    Args:
        producto: product name (e.g. "whole milk").
        supermercado: supermarket where the price was observed.
        precio: observed price in euros.
    """
    try:
        db.update_price(producto, supermercado, precio)
    except Exception as exc:  # noqa: BLE001
        return f"Error logging the price of '{producto}' ({supermercado}): {exc}"

    return (
        f"Logged: {producto} at {precio:.2f} EUR at {supermercado}. "
        "Updated the list's last known price and added it to price history."
    )


@mcp.tool()
def query_cheapest(producto: str) -> str:
    """Checks where a product is cheapest RIGHT NOW, per supermarket.

    Returns the MOST RECENT known price at each supermarket (from price history),
    sorted cheapest to most expensive -- not the lowest price it's ever had. Only
    includes supermarkets that have at least one logged price for that product.

    Useful for requests like "where's milk cheapest?" or "how much does bread cost at
    each supermarket?".

    Args:
        producto: product name to look up (e.g. "whole milk"). If no price has been
            logged for it, a message says so.
    """
    try:
        results = db.query_cheapest(producto)
    except Exception as exc:  # noqa: BLE001
        return f"Error querying prices for '{producto}': {exc}"

    if not results:
        return (
            f"No price has been logged for '{producto}' yet. "
            "Log one with update_price when one is observed."
        )

    lines = [f"Most recent prices for {producto} (cheapest to most expensive):"]
    for i, (supermercado, precio) in enumerate(results, start=1):
        lines.append(f"{i}. {supermercado}: {precio:.2f} EUR")
    return "\n".join(lines)


@mcp.tool()
def list_current() -> str:
    """Shows everything currently on the shopping list.

    Returns each item with its product, supermarket, last known price (if any), and
    when it was last updated. Useful for requests like "what's on the list?" or "show
    me the shopping list", and also to check exactly how a product is stored before
    removing it or updating its price.
    """
    try:
        items = db.list_current()
    except Exception as exc:  # noqa: BLE001
        return f"Error reading the shopping list: {exc}"

    if not items:
        return "The shopping list is empty."

    lines = ["Shopping list:"]
    for item in items:
        if item["ultimo_precio"] is None:
            lines.append(f"- {item['producto']} ({item['supermercado']}): no price yet")
        else:
            lines.append(
                f"- {item['producto']} ({item['supermercado']}): "
                f"{item['ultimo_precio']:.2f} EUR (updated {item['actualizado_en']})"
            )
    return "\n".join(lines)


@mcp.tool()
async def process_receipt_photo(imagen_base64: str) -> str:
    """Processes a photo of a shopping receipt: runs OCR, identifies the products and
    their real prices (with discounts already applied), and updates the shopping list
    -- logs the price to history and removes from the list any products that were
    already on it. Products with genuine ambiguity (unclear OCR text, several possible
    matches on the list, etc.) are NOT touched automatically: they're returned
    separately for the user to review by hand.

    Triggers the "shopping-receipt" Workflow (Mistral Workflows) and waits for its
    result -- can take a few seconds (it runs OCR and a model call before responding).

    Useful for requests like "I've uploaded the receipt photo, process it" when the
    user attaches a photo of a supermarket receipt in the conversation.

    THIS IS THE ONLY CORRECT WAY to process a receipt, even though you can see the
    image yourself: do NOT read the prices off the photo and log them by hand with
    update_price -- a model eyeballing a receipt gets the discount/promotion
    arithmetic wrong (verified in real tests). Always pass the image to this tool and
    let it compute the final prices.

    Args:
        imagen_base64: the receipt photo's content, base64-encoded (just the image
            bytes, without the "data:image/...;base64," prefix).
    """
    try:
        client = get_mistral_client(api_key=MISTRAL_API_KEY, server_url=WORKFLOWS_SERVER_URL)
        await configure_workflow_encoding(WorkflowEncodingConfig(), client=client)
        result = await client.workflows.execute_workflow_and_wait_async(
            workflow_identifier="shopping-receipt",
            input={"imagen_base64": imagen_base64},
            deployment_name=WORKFLOWS_DEPLOYMENT_NAME,
        )
    except Exception as exc:  # noqa: BLE001 - readable message, not a stack trace
        return f"Error processing the receipt: {exc}"

    return str(result)


# ---------------------------------------------------------------------------
# Bearer-token auth ASGI middleware, and server startup
# ---------------------------------------------------------------------------


class BearerAuthMiddleware:
    """Pure ASGI middleware that requires `Authorization: Bearer <token>` on /mcp.

    Applied before dispatching to the MCP handler: if the header is missing or
    doesn't match MCP_AUTH_TOKEN, it responds with 401 directly without invoking the
    wrapped app. "lifespan" events are passed through without any check (they're
    process start/stop, not HTTP requests), so FastMCP's StreamableHTTPSessionManager
    starts and stops correctly.
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
                {"error": "unauthorized", "detail": "Missing or invalid Authorization: Bearer <token> header."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# Underlying ASGI app exposing FastMCP over Streamable HTTP, mounted at "/mcp"
# (settings.mount_path="/" + settings.streamable_http_path="/mcp" -> final path "/mcp").
_streamable_http_app = mcp.streamable_http_app()

# Final app: auth middleware wrapping the MCP app.
app = BearerAuthMiddleware(_streamable_http_app, MCP_AUTH_TOKEN)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
