# Lista de la compra + histórico de precios (vía Mistral AI)

Lista de la compra personal con histórico de precios por supermercado, controlable por
lenguaje natural desde el chat de Mistral (Le Chat), con procesado de fotos de tickets como
funcionalidad adicional. Proyecto de aprendizaje sobre Mistral Workflows/Agents, self-hosted en
`vserver` (ver [`Server/CLAUDE.md`](https://github.com/victorjdg/vserver) para la topología del
servidor).

Ver [`PLAN.md`](./PLAN.md) para el historial completo de decisiones, hallazgos y fases — este
README es solo el resumen de qué es y cómo se despliega.

## Qué hace

- **Conversacional** (`add_item`, `remove_item`, `update_price`, `query_cheapest`,
  `list_current`): añadir/quitar productos, registrar precios observados y consultar dónde está
  más barato algo ahora mismo, todo por chat.
- **Procesado de tickets** (`process_receipt_photo`): sube una foto de un ticket y, en teoría,
  hace OCR + calcula los precios reales con descuentos aplicados + actualiza la lista y el
  histórico. **Con una limitación real e importante, ver más abajo.**

## ⚠️ Limitación conocida: el procesado de tickets no pasa por el Workflow en la práctica

`process_receipt_photo` dispara un Workflow (`shopping-receipt`) que hace OCR y calcula bien los
descuentos/promociones (verificado con un ticket real). Pero **Le Chat, al ver una foto
adjunta, la procesa con su propia visión y llama directamente a `update_price` línea por línea,
sin pasar por la tool** — no es un problema de instrucciones (se reforzaron las descripciones de
las tools y siguió pasando dos veces seguidas): un modelo que "ve" una imagen no tiene acceso a
sus bytes crudos, así que no puede reproducir un base64 válido como argumento de una tool call.

**Efecto real**: los tickets con descuentos/promociones pueden quedar en el histórico con el
precio de catálogo, no el precio real pagado — revisar a mano si hace falta precisión. Detalle
completo y la alternativa que se descartó (endpoint HTTP propio fuera del chat) en la sección
Fase 4 de `PLAN.md`.

## Arquitectura

```
Le Chat (Mistral) ── Connector MCP ──► lista.victorjdg.com (mcp/, este repo)
                                              │
                                              ├─ 5 tools CRUD/consulta → db/ (SQLite)
                                              │
                                              └─ process_receipt_photo
                                                     │ dispara (SDK Mistral Workflows)
                                                     ▼
                                        mistral-workflow-worker (Server/podman/mistral-workflow/)
                                              │  workflow "shopping-receipt"
                                              │  (OCR + cálculo de descuentos + llama de
                                              │   vuelta a lista.victorjdg.com por MCP)
                                              ▼
                                        db/ (SQLite, /mnt/storage/appdata/shopping-list)
```

El worker de Mistral Workflows es **infraestructura compartida** con otro proyecto
(`server-health-report`, un informe diario del estado del servidor) — vive y se despliega desde
el repo `Server` (`podman/mistral-workflow/`), no desde aquí. Este repo mantiene su propia copia
versionada de `workflow/shopping_receipt.py`; si se edita, replicar el cambio en las dos copias
(ver comentario en el fichero).

## Estructura del repo

| Directorio | Qué es | Despliegue |
|---|---|---|
| `db/` | Schema SQLite + capa de acceso (`db.py`, `initdb.py`) | Se importa tal cual desde `mcp/` |
| `mcp/` | Servidor MCP (`server.py`), expone las 6 tools | `01-deploy.sh` → contenedor `shopping-list`, `https://lista.victorjdg.com` |
| `workflow/` | `shopping_receipt.py` — copia versionada, el worker real vive en `Server/podman/mistral-workflow/` | Ver ese repo |
| `backup/` | `backup.py` — backup diario del SQLite vía cron | `backup/README.md` |
| `secrets/` | Tokens/credenciales, no versionado (`.gitignore`) | — |
| `PLAN.md` | Historial completo de decisiones y hallazgos por fase | — |

## Desplegar desde cero

```bash
# 1. MCP server (lista.victorjdg.com)
cp secrets/mcp-token.env.example secrets/mcp-token.env   # rellenar MCP_AUTH_TOKEN y MISTRAL_API_KEY
./01-deploy.sh

# 2. Worker de Mistral Workflows (compartido, ver Server/podman/mistral-workflow/README.md)

# 3. Backups (ver backup/README.md)
```

Falta aparte (vive en el repo `Server`, no aquí): bloque del Caddyfile para
`lista.victorjdg.com` y jail de `fail2ban` — ver `Server/CLAUDE.md`, sección "Lista de la
compra vía Mistral".

## Registrar el Connector en Mistral Studio

Mismos pasos que `mcp-server` — ver
[`Server/podman/mcp-server/REGISTRO-MISTRAL.md`](https://github.com/victorjdg/vserver/blob/main/podman/mcp-server/REGISTRO-MISTRAL.md),
sustituyendo la URL por `https://lista.victorjdg.com/mcp`. **Ojo con el prefijo `Bearer`**: el
campo de valor de la cabecera en Mistral Studio no lo añade solo, hay que escribirlo a mano
(`Bearer <token>`) — ver esa misma guía.
