# Shopping List MCP + Workflow

A personal shopping list with price history per supermarket, controllable entirely by natural
language through an LLM (I use it from Mistral AI's Le Chat), plus a Mistral Workflow that
processes a photo of a receipt: OCR, matches each line against the current list, and computes
the real price after discounts.

It's built as a **[Model Context Protocol](https://modelcontextprotocol.io) server** — the LLM
never touches the database directly, it only calls tools and gets a text result back.

## ⚠️ A known limitation

The receipt-photo tool (`process_receipt_photo`) exists and works when called directly, but in
practice a chat client with vision (like Mistral's Le Chat) sees an attached photo and processes
it **with its own vision** instead of calling the tool — it turns out a model can't reliably
reproduce the real bytes of an image it only "sees" as a valid base64 tool-call argument. The
practical effect: receipts with discounts or multi-buy promotions can end up in the price
history at catalog price instead of the real discounted price, because the model does the
arithmetic itself instead of going through the Workflow. Documented here rather than papered
over — a good reminder that "the model can see it" and "the model can pass it to a tool" aren't
the same thing.

## Architecture

```
LLM chat client ── MCP (Streamable HTTP, Bearer auth) ──► mcp/server.py
                                                                │
                                                                ├─ 5 tools ──► db/ (SQLite)
                                                                │
                                                                └─ process_receipt_photo
                                                                       │ triggers (Mistral
                                                                       │ Workflows SDK)
                                                                       ▼
                                                        Workflow: OCR + discount math
                                                        + calls back into the 5 tools above
```

The Workflow needs a Mistral Workflows **worker** process running somewhere to actually execute
(not included in this repo — it's a small piece of shared infrastructure, see
`workflow/README.md`). Everything else here is self-contained.

## Tools

| Tool | What it does |
|---|---|
| `add_item(producto, supermercado, precio?)` | Adds a product to the list. Upserts on `(producto, supermercado)` — doesn't duplicate if it's already there. The optional price is stored only as the list's "last known price," **not** logged to price history (use `update_price` for that). |
| `remove_item(producto, supermercado)` | Removes a product from the list. Price history is untouched — a removed product still answers `query_cheapest`. |
| `update_price(producto, supermercado, precio)` | Logs an observed price: updates the list's last-known price *and* appends a row to price history, atomically. The right tool whenever a real price was just seen, whether or not the product is currently on the list. |
| `query_cheapest(producto)` | Returns the most recent known price at each supermarket for a product, cheapest first — not the historical minimum, the latest one. |
| `list_current()` | Shows everything currently on the list, with last known price and when it was last updated. |
| `process_receipt_photo(imagen_base64)` | Triggers the `shopping-receipt` Mistral Workflow: OCRs the photo, matches each line item against the current list, computes the real per-unit price after discounts in code (never trusting an LLM with that arithmetic — verified that it silently gets it wrong), and applies high-confidence matches automatically. See the limitation above. |

## Repo layout

| Directory | What's in it |
|---|---|
| `db/` | SQLite schema + access layer (`db.py`, `initdb.py`) |
| `mcp/` | The MCP server (`server.py`) — the 6 tools above |
| `workflow/` | `shopping_receipt.py`, the Mistral Workflow definition |
| `backup/` | Daily SQLite backup script (`sqlite3.Connection.backup()` + rotation) |
| `secrets/` | Tokens, `.gitignore`'d — see `secrets/*.env.example` |

## Deploying

```bash
cp secrets/mcp-token.env.example secrets/mcp-token.env
# fill in MCP_AUTH_TOKEN (random token clients authenticate with) and MISTRAL_API_KEY
./01-deploy.sh
```

Builds a container from `mcp/Dockerfile` (context: repo root, since it imports `db/` as-is),
publishes on loopback only, and expects a reverse proxy with HTTPS in front for real use. See
`config.env` for the volume/port layout, and `backup/README.md` for the backup cron job.

## Registering as an MCP Connector (Mistral Studio)

1. Studio → **Connectors** → new Connector.
2. Server URL: your deployed `/mcp` endpoint.
3. Headers → `Authorization` → `Bearer <your MCP_AUTH_TOKEN>`.

**Gotcha**: the header *value* field in Mistral Studio does not prepend `Bearer` for you — type
the literal string `Bearer <token>` (with the space), not just the token. Confirmed live: Studio
was sending the bare 48-character token instead of the 55-character `Bearer <token>`, causing
every call to 401 until fixed.
