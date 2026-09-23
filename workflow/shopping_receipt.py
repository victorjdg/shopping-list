"""Procesa la foto de un ticket de compra: OCR + matching contra la lista actual (mismo MCP
server de shopping-list, via execute_mcp_tool) + registro de precios + limpieza de la lista.
Ver shopping-list/PLAN.md (Fase 3) para el diseno completo.

No toca SQLite directamente -- llama a las tools ya desplegadas en lista.victorjdg.com
(list_current, update_price, remove_item), mismo patron que server_health.py contra
mcp-server. Las unicas piezas de logica nueva son el OCR y el parseo+matching con salida
estructurada -- ambos delegan en activities oficiales del plugin
(mistralai_ocr, chat_parse_to_model sobre mistralai_chat_parse), sin envolverlas en activities
propias: igual que execute_mcp_tool, ya son activities por si mismas, envolverlas de nuevo solo
anidaria sin aportar nada (ver "Nested Activities" en la guia de referencia del scaffolding).
"""

import os
from typing import Literal

import mistralai.workflows as workflows
import mistralai.workflows.plugins.mistralai as workflows_mistralai
from mistralai.client.models.imageurlchunk import ImageURLChunk
from mistralai.workflows.plugins.mistralai.mcp import ExecuteMCPToolParams
from pydantic import BaseModel

# Config del cliente MCP contra el servidor de la lista de la compra (repo shopping-list,
# desplegado en lista.victorjdg.com) -- mismo patron que _MCP_CONFIG en server_health.py, pero
# apuntando a otro servidor. Nombres de env var DISTINTOS de los que usa server_health.py
# contra mcp-server, para no chocar en el mismo .env.
_SHOPPING_MCP_CONFIG = workflows_mistralai.MCPStreamableHTTPConfig(
    name="lista",
    url=os.environ.get("SHOPPING_LIST_MCP_URL", "http://host.containers.internal:8001/mcp"),
    auth_token_env="SHOPPING_LIST_MCP_TOKEN",
)


async def _call_shopping_tool(tool_name: str, arguments: dict) -> str:
    """Llama a una tool del MCP server de la lista de la compra. Mismo wrapper fino que
    _call_tool en server_health.py, apuntando a otro config."""
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
    """OCR del ticket via mistral-ocr-latest. Devuelve el markdown de todas las paginas
    concatenado (un ticket normal es 1 pagina, pero por si acaso)."""
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
        """Precio unitario REAL pagado -- calculado en Python, no por el modelo. Pedirle a un
        LLM que sume descuentos y divida entre cantidad dentro del propio prompt es fragil (se
        verifico en vivo con un ticket real: el modelo fallaba la aritmetica en mas de una
        linea). El modelo solo extrae los numeros en crudo del ticket; el calculo determinista
        vive aqui."""
        cantidad = max(self.cantidad, 1)
        return round((self.precio_bruto + sum(self.descuentos)) / cantidad, 2)


class TicketMatches(BaseModel):
    lineas: list[TicketLine]


async def _parse_ticket(texto_ocr: str, lista_actual: str) -> TicketMatches:
    """Convierte el texto OCR del ticket en lineas estructuradas, decidiendo por cada una si
    coincide con algo que ya estaba en la lista actual -- delegado al modelo (ver PLAN.md,
    Fase 0: "como se identifica el mismo producto"), no a reglas de texto.

    El modelo solo EXTRAE numeros del texto (precio_bruto, descuentos, cantidad) -- no hace
    ninguna suma/resta/division. El calculo del precio unitario final vive en
    TicketLine.precio_unitario, en Python."""
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
    workflow_display_name="Procesar ticket de compra",
    workflow_description=(
        "OCR de una foto de ticket, matching contra la lista de la compra actual, registro "
        "de precios en el historico y limpieza de los productos ya comprados."
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
            precio = linea.precio_unitario  # calculado en Python, ver TicketLine.precio_unitario
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
                aplicados.append(f"{linea.producto} ({linea.supermercado}): {precio:.2f} €")
            else:
                revisar.append(f"{linea.producto} ({linea.supermercado}): {precio:.2f} €")

        partes = []
        if aplicados:
            partes.append("Aplicado automáticamente:\n" + "\n".join(f"- {a}" for a in aplicados))
        if revisar:
            partes.append(
                "Revisar a mano (confianza baja, no se ha tocado la lista):\n"
                + "\n".join(f"- {r}" for r in revisar)
            )
        return "\n\n".join(partes) if partes else "No se detectó ningún producto en el ticket."
