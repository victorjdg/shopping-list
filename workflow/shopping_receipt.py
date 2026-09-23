"""Processes a photo of a shopping receipt: OCR + matching against the current list (the
same shopping-list MCP server, via execute_mcp_tool) + price logging + list cleanup.

Doesn't touch SQLite directly -- calls the tools already deployed on the shopping-list MCP
server (list_current, update_price, remove_item), same pattern as any other workflow that
talks to an MCP server via execute_mcp_tool. The only genuinely new pieces of logic are OCR
and the structured-output parsing+matching -- both delegate to official activities of the
plugin (mistralai_ocr, chat_parse_to_model over mistralai_chat_parse), without wrapping them
in activities of their own: like execute_mcp_tool, they're already activities by themselves,
wrapping them again would only nest without adding anything (see "Nested Activities" in the
Mistral Workflows reference docs).

Note: the model prompt below is in Spanish on purpose -- it processes real Spanish
supermarket receipts and a shopping list with Spanish product/supermarket names, and keeping
the instruction language aligned with the data it reads and writes avoids adding a second,
unnecessary translation step for the model.
"""

import os
from typing import Literal

import mistralai.workflows as workflows
import mistralai.workflows.plugins.mistralai as workflows_mistralai
from mistralai.client.models.imageurlchunk import ImageURLChunk
from mistralai.workflows.plugins.mistralai.mcp import ExecuteMCPToolParams
from pydantic import BaseModel

# Config for the MCP client against the shopping-list server -- same pattern as any other
# MCP client config, just pointing at a different server. Env var names are DISTINCT from
# any other MCP client config in the same worker, so they don't collide in the same .env.
_SHOPPING_MCP_CONFIG = workflows_mistralai.MCPStreamableHTTPConfig(
    name="lista",
    url=os.environ.get("SHOPPING_LIST_MCP_URL", "http://host.containers.internal:8001/mcp"),
    auth_token_env="SHOPPING_LIST_MCP_TOKEN",
)


async def _call_shopping_tool(tool_name: str, arguments: dict) -> str:
    """Calls a tool on the shopping-list MCP server. A thin wrapper around
    execute_mcp_tool, pointed at the config above."""
    result = await workflows_mistralai.execute_mcp_tool(
        ExecuteMCPToolParams(
            configs=[_SHOPPING_MCP_CONFIG],
            tool_name=f"{_SHOPPING_MCP_CONFIG.name}_{tool_name}",
            tool_arguments=arguments,
            config_index=0,
        )
    )
    return result.result


async def _ocr_ticket(imagen_base64: str) -> str:
    """OCRs the receipt via mistral-ocr-latest. Returns the markdown of every page
    concatenated (a normal receipt is 1 page, but just in case)."""
    response = await workflows_mistralai.mistralai_ocr(
        workflows_mistralai.OCRRequest(
            model="mistral-ocr-latest",
            document=ImageURLChunk(image_url=f"data:image/jpeg;base64,{imagen_base64}"),
        )
    )
    return "\n\n".join(page.markdown for page in response.pages)


class TicketLine(BaseModel):
    producto: str
    supermercado: str
    precio_bruto: float
    descuentos: list[float]
    cantidad: int
    en_lista_actual: bool
    confianza: Literal["alta", "baja"]

    @property
    def precio_unitario(self) -> float:
        """REAL unit price paid -- computed in Python, not by the model. Asking an LLM
        to sum discounts and divide by quantity inline in the prompt is fragile (verified
        live with a real receipt: the model got the arithmetic wrong on more than one
        line). The model only extracts the raw numbers from the receipt; the
        deterministic calculation lives here."""
        cantidad = max(self.cantidad, 1)
        return round((self.precio_bruto + sum(self.descuentos)) / cantidad, 2)


class TicketMatches(BaseModel):
    lineas: list[TicketLine]


async def _parse_ticket(texto_ocr: str, lista_actual: str) -> TicketMatches:
    """Turns the receipt's OCR text into structured lines, deciding for each one
    whether it matches something already on the current list -- delegated to the model
    (identifying "the same product" from free text is ambiguous, no fixed rule covers
    it), not to text rules.

    The model only EXTRACTS numbers from the text (precio_bruto, descuentos, cantidad)
    -- it does no addition/subtraction/division. The final unit price calculation lives
    in TicketLine.precio_unitario, in Python."""
    prompt = (
        "Eres un asistente que extrae productos y precios de un ticket de supermercado ya "
        "pasado por OCR, y los compara con la lista de la compra actual del usuario.\n\n"
        "Para CADA producto que aparezca en el ticket, decide:\n"
        "- producto: el nombre a usar. Si coincide con algo de la lista actual, usa EXACTAMENTE "
        "el mismo texto que en la lista (no el texto crudo del ticket) -- es la clave que se "
        "usara para actualizar la base de datos.\n"
        "- supermercado: normalmente aparece en la cabecera del ticket (nombre/logo de la "
        "tienda). Si coincide con la lista, usa el mismo texto que en la lista.\n"
        "- precio_bruto: el precio de la linea principal de ese producto, TAL CUAL aparece en "
        "el ticket, sin restar nada tu mismo (p. ej. si la linea dice \"8,98\", precio_bruto "
        "es 8.98 -- no calcules nada aqui).\n"
        "- descuentos: lista de los importes de descuento asociados a ese producto (lineas "
        "como \"Desc.\", \"Descuento X%\", \"PROMO ...\" que aparecen justo debajo, con signo "
        "NEGATIVO tal cual aparecen en el ticket). Lista vacia si no hay ninguno. No los sumes "
        "tu mismo, devuelve cada importe tal cual esta escrito.\n"
        "- cantidad: el numero de unidades de esa linea, si el ticket lo indica (formato "
        "\"PRECIO x CANTIDAD\" o similar). Si no se indica cantidad, usa 1.\n"
        "- en_lista_actual: true si ese producto+supermercado ya estaba en la lista actual.\n"
        "- confianza: 'alta' solo si estas seguro del producto, supermercado, precio_bruto y "
        "descuentos. 'baja' si hay cualquier ambiguedad en el texto del OCR (letra poco clara, "
        "producto que podria ser varios de la lista, numero dudoso, etc.) -- ante la duda, usa "
        "'baja'. Una linea sin descuentos y con texto claro puede ser 'alta' con normalidad.\n\n"
        f"Lista de la compra actual:\n{lista_actual}\n\n"
        f"Texto del ticket (OCR):\n{texto_ocr}"
    )
    return await workflows_mistralai.chat_parse_to_model(
        TicketMatches,
        workflows_mistralai.ChatCompletionRequest(
            model="mistral-medium-latest",
            messages=[workflows_mistralai.UserMessage(content=prompt)],
        ),
    )


class ProcessReceiptInput(BaseModel):
    imagen_base64: str


@workflows.workflow.define(
    name="shopping-receipt",
    workflow_display_name="Process shopping receipt",
    workflow_description=(
        "OCR of a receipt photo, matching against the current shopping list, logging "
        "prices to history, and cleaning up products already bought."
    ),
)
class ProcessReceiptWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: ProcessReceiptInput) -> str:
        texto_ocr = await _ocr_ticket(input.imagen_base64)
        lista_actual = await _call_shopping_tool("list_current", {})
        matches = await _parse_ticket(texto_ocr, lista_actual)

        aplicados = []
        revisar = []
        for linea in matches.lineas:
            precio = linea.precio_unitario  # computed in Python, see TicketLine.precio_unitario
            if linea.confianza == "alta":
                await _call_shopping_tool(
                    "update_price",
                    {
                        "producto": linea.producto,
                        "supermercado": linea.supermercado,
                        "precio": precio,
                    },
                )
                if linea.en_lista_actual:
                    await _call_shopping_tool(
                        "remove_item",
                        {"producto": linea.producto, "supermercado": linea.supermercado},
                    )
                aplicados.append(f"{linea.producto} ({linea.supermercado}): {precio:.2f} EUR")
            else:
                revisar.append(f"{linea.producto} ({linea.supermercado}): {precio:.2f} EUR")

        partes = []
        if aplicados:
            partes.append("Applied automatically:\n" + "\n".join(f"- {a}" for a in aplicados))
        if revisar:
            partes.append(
                "Needs manual review (low confidence, list untouched):\n"
                + "\n".join(f"- {r}" for r in revisar)
            )
        return "\n\n".join(partes) if partes else "No product was detected on the receipt."
