# Lista de la compra + histórico de precios — vía Mistral (Agent + Workflow)

Plan de trabajo. Victor lo implementa; Claude ayuda con dudas puntuales según se vayan
pidiendo — esta lista se va marcando (`[x]`) a medida que se completa cada paso, no se
implementa todo de golpe.

## Decisiones ya tomadas (contexto para no repetir la discusión)

- **Arquitectura: Agent + Workflow, no "un Workflow solo".** El Agent (function-calling normal)
  atiende la parte conversacional rápida (añadir/quitar/consultar). El Workflow atiende
  específicamente el procesado del ticket — es la parte con varios pasos y que se beneficia de
  reintentos automáticos (OCR puede fallar, hay varias escrituras encadenadas).
- **Almacén: SQLite**, no JSON sueltos — hace falta poder consultar "precio mínimo de X
  agrupado por supermercado" sin cargar y escanear todo a mano, y evita condiciones de carrera
  si el Agent y el Workflow escriben a la vez.
- **Mistral nunca toca la base de datos directamente.** El fichero SQLite vive en el contenedor
  dedicado de este proyecto, en la máquina donde corre tu propio código. Mistral solo manda
  "ejecuta esta tool/activity" y recibe de vuelta el resultado ya procesado — nunca el fichero,
  nunca credenciales de acceso a él.
- **OCR nativo**: `mistralai_ocr` ya es una activity oficial del plugin de Mistral Workflows —
  no hace falta montar un servicio de OCR aparte.

## Decisiones de la Fase 0 (2026-09-23)

- **Worker + SQLite en un contenedor Podman dedicado**, separado de `mcp-server` — no se
  reutiliza ese servicio. Asunción de trabajo: se despliega en `vserver` (es el único servidor
  que hay) — corregir si no es así.
- **Las tools del Agent se montan desde cero, a mano por Victor** — no se extiende
  `mcp-server`. El código de este proyecto (tools del Agent, activities del Workflow, acceso a
  SQLite) vive en este mismo directorio (`shopping-list/`), como proyecto propio — mismo
  patrón que `deep-cli`, no anidado dentro del repo `Server`.
- Queda por decidir (ver checkboxes de abajo): nombre concreto del servicio/contenedor, y si el
  contenedor expone el Agent+tools y el Workflow+SQLite juntos o en dos contenedores separados
  (probablemente juntos para empezar — un único contenedor con SQLite + tools + worker de
  Workflows — y separar más adelante solo si hace falta).

---

## Fase 0 — Diseño y decisiones previas

- [x] Decidir dónde vive el worker/servicio → contenedor Podman dedicado, nuevo, en `vserver`
- [x] Decidir dónde vive el fichero SQLite → mismo contenedor (volumen `appdata` propio)
- [x] Nombre del proyecto/servicio/contenedor → `shopping-list`
- [x] Diseñar el esquema exacto de las tablas:

  ```sql
  CREATE TABLE lista_compra (
      id INTEGER PRIMARY KEY,
      producto TEXT NOT NULL,
      supermercado TEXT NOT NULL,      -- parte de la IDENTIDAD del item, no un atributo suelto
      ultimo_precio REAL,              -- precio de ESE producto en ESE super
      actualizado_en TEXT NOT NULL,    -- ISO 8601
      UNIQUE(producto, supermercado)
  );

  CREATE TABLE historico_precios (
      id INTEGER PRIMARY KEY,
      producto TEXT NOT NULL,          -- sin FK estricta a lista_compra: debe sobrevivir aunque
      supermercado TEXT NOT NULL,      -- el producto ya no este en la lista actual
      precio REAL NOT NULL,
      fecha TEXT NOT NULL              -- ISO 8601
  );
  ```

  Nota para la Fase 2: cuando el usuario diga "añade X" por chat sin especificar súper, el
  Agent tendrá que preguntar o usar un valor por defecto (`"sin especificar"`) — decidir al
  implementar esa tool, no bloquea la Fase 0.

- [x] Cómo se identifica "el mismo producto" → **delegado al modelo, no a reglas de texto**:
  - En el chat conversacional (Fase 2) ya es gratis — el Agent interpreta el lenguaje natural
    y llama a `add_item`/etc. con parámetros ya limpios.
  - En el procesado del ticket (Fase 3), la activity de parseo recibe el texto OCR **+ el
    contenido actual de `lista_compra`** en el mismo prompt, y devuelve (structured outputs)
    para cada línea del ticket: coincide con el ítem X de la lista, o es nuevo — con un nivel
    de confianza. Confianza alta → se aplica solo. Confianza baja → se pregunta al usuario por
    chat antes de tocar la base de datos. Sustituye al plan anterior de normalizar texto a
    mano; se fusiona con el paso de parseo en vez de ser un paso aparte.

## Fase 1 — Base de datos ✅ (2026-09-23)

- [x] Crear el fichero SQLite y las tablas (schema) → `db/schema.sql` + `db/initdb.py`
- [x] Escribir funciones Python mínimas de acceso (CRUD) → `db/db.py`: `add_item`,
      `remove_item`, `update_price`, `query_cheapest`, `list_current`
- [x] Probarlas → `test_db.py`, 18 tests, todos en verde (incluye un test con `TRIGGER` de
      SQLite forzando un fallo para comprobar que `update_price` hace rollback atómico de
      verdad)
- Nota para la Fase 2: `add_item(precio=...)` NO escribe en `historico_precios` (solo
  `update_price` lo hace) — decidir explícitamente en la tool del Agent si añadir con precio
  debe contar también como observación de precio.

## Fase 2 — Agent conversacional (consulta y actualización por lenguaje natural) ✅ (2026-09-23)

- [x] Decidir dónde exponer las tools nuevas → servicio nuevo propio, montado a mano (no se
      extiende `mcp-server`)
- [x] Implementar las tools: `add_item`, `remove_item`, `update_price`, `query_cheapest`,
      `list_current` → `mcp/server.py`, mismo patrón que `mcp-server` (FastMCP + Streamable
      HTTP + Bearer). **Verificado end-to-end en local** (venv con `mcp<2`+`uvicorn`): auth
      401/401, handshake OK, y el flujo completo de las 5 tools con datos reales — incluida la
      ruta override de `SHOPPING_LIST_DB` funcionando correctamente (no cae al fallback de
      `db/`).
  - Puerto `8001` en el host (`8000` ya lo usa `mcp-server`), dominio `lista.victorjdg.com`.
  - [x] Desplegado en `vserver` (2026-09-23): `~/shopping-list-stack`, contenedor
    `shopping-list` corriendo, verificado con el token real end-to-end (401 sin token, las 5
    tools respondiendo con datos reales sobre el volumen `appdata` de verdad).
  - [x] Bloque del `Caddyfile` añadido y redesplegado — certificado HTTPS obtenido a la
    primera (el DNS ya resolvía), confirmado desde fuera: `https://lista.victorjdg.com/mcp` →
    401 real.
  - [x] Filtro de `fail2ban` actualizado en el repo (reusa la jail `mcp-auth` existente,
    añadido un segundo `failregex` para el host nuevo).
  - ⚠️ **Pendiente, requiere a Victor** (sudo con contraseña, no se puede por SSH no
    interactivo): instalar el filtro nuevo y recargar la jail —

    ```bash
    ssh homeserver
    sudo cp ~/mcp-auth.conf.new /etc/fail2ban/filter.d/mcp-auth.conf
    sudo fail2ban-client reload mcp-auth
    rm ~/mcp-auth.conf.new
    ```

- [x] Crear/registrar el Connector en Mistral Studio apuntando a
      `https://lista.victorjdg.com/mcp` — **conectado y verificado** (2026-09-23): `200
      OK`/`202 Accepted` en el log del contenedor tras el fix.
  - **Gotcha real encontrado y resuelto**: el primer intento fallaba con 401 en todas las
    llamadas — Mistral Studio mandaba el token **sin el prefijo `Bearer `** (48 caracteres
    recibidos vs 55 esperados, diagnosticado con un log temporal sin exponer el secreto).
    Arreglo: el campo de **valor** de la cabecera en Studio tiene que ser literalmente
    `Bearer <token>`, con el prefijo escrito a mano — la UI no lo añade sola. Mismo campo
    ambiguo que ya se había marcado como "no confirmado" en
    `podman/mcp-server/REGISTRO-MISTRAL.md`.
- [x] Probar en el chat de Mistral: confirmado por Victor, funciona bien (añadir, listar,
      consultar precio más barato).
- [x] Filtro de `fail2ban` instalado y jail recargada (paso manual de Victor, completado).

## Fase 3 — Workflow del ticket (la parte más compleja)

- [ ] Decidir cómo llega la imagen al workflow (base64 directo, file_id de la Files API de
      Mistral, URL...)
- [ ] Scaffolding del proyecto de Workflows (o reusar el de `mistral-workflow`/
      `server-health-report` si se prefiere un único proyecto)
- [ ] Activity: OCR del ticket (`mistralai_ocr`)
- [ ] Activity: parseo + matching en un solo paso — texto OCR + contenido actual de
      `lista_compra` → structured outputs con, por cada línea del ticket: producto,
      supermercado, precio, `matched_item_id` (o null si es nuevo), y confianza del match
- [ ] Activity: escribir cada línea en `historico_precios`
- [ ] Activity: actualizar `último_precio` en `lista_compra` para los matches de confianza alta
- [ ] Activity: eliminar de `lista_compra` los ítems de confianza alta ya comprados
- [ ] Manejar los de confianza baja: no auto-aplicar, preguntar al usuario por chat antes de
      tocar la base de datos
- [ ] Probar con una foto de ticket real

## Fase 4 — Conectar Agent y Workflow

- [ ] Mirar `durable-agents.mdx` (en la documentación de referencia que trae el scaffolding de
      Mistral Workflows) para ver si cubre este patrón de forma nativa
- [ ] Añadir al Agent conversacional una tool `process_receipt_photo(imagen)` que dispare la
      ejecución del Workflow
- [ ] Probar el flujo completo desde el chat: subir una foto → confirmar que se actualizan
      lista y histórico correctamente

## Fase 5 — Despliegue y robustez

- [ ] Contenerizar el worker (Dockerfile + script de despliegue, `--restart=always`)
- [ ] Backups del fichero SQLite (aunque sea una copia periódica simple)
- [ ] Revisar manejo de errores/reintentos en cada activity
- [ ] Documentar el proyecto (README)
