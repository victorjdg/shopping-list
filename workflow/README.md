# `shopping-receipt` Workflow

`shopping_receipt.py` defines a [Mistral Workflow](https://docs.mistral.ai/agents/workflows/)
(durable execution built on Temporal) that processes a photo of a supermarket receipt:

1. **OCR** the image (`mistralai_ocr`, a built-in activity of the Mistral Workflows plugin).
2. **Parse + match** the OCR text into structured lines with a model, deciding per line whether
   it matches something already on the shopping list. The model only extracts raw numbers
   (gross price, discounts, quantity) — it never does the arithmetic itself. The real per-unit
   price is computed in plain Python (`TicketLine.precio_unitario`), because in testing an LLM
   asked to do that math inline got it wrong on every discounted line, silently.
3. **Apply** high-confidence matches by calling back into the MCP server's tools
   (`update_price`, `remove_item`) — same protocol, no direct database access. Low-confidence
   lines are left alone and returned for manual review instead.

## Running it

This file only defines the workflow — it needs a **worker process** to actually execute (the
official `mistralai-workflows` SDK, which auto-discovers and registers workflow classes, then
connects to Mistral's managed Temporal-based scheduler). That worker is a small piece of shared
infrastructure, not included in this repo. To run it yourself:

```bash
pip install "mistralai-workflows[mistralai]>=3.0.0,<4" pydantic python-dotenv
```

Point a worker's auto-discovery at a package containing this file, set `MISTRAL_API_KEY` and
`SHOPPING_LIST_MCP_URL`/`SHOPPING_LIST_MCP_TOKEN` (pointing at your deployed `mcp/server.py`),
and the SDK's `run_worker(...)` entrypoint takes care of registration and execution.

See the top-level README for the known limitation with how (or rather, how *not*) this gets
triggered from a chat client.
