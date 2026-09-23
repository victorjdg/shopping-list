#!/bin/bash
# Construye la imagen del MCP server de la lista de la compra en local (Dockerfile
# propio en ./mcp, con contexto de build la raíz del proyecto porque importa db/
# tal cual) y la despliega. Publicado solo en 127.0.0.1:$PORT a propósito (no en
# toda la LAN) -- Caddy corre en --network host y lo alcanza igualmente por
# loopback, así queda protegido detrás de MCP_AUTH_TOKEN sin exponerse directo en
# la red local. Pensado para conectarse como "Connector" desde Mistral AI.
#
# El fichero SQLite vive en $APPDATA_ROOT (volumen appdata propio), montado en
# /appdata del contenedor -- sobrevive a reconstrucciones del contenedor.
#
# Falta aparte (viven en el repo Server, no aquí): bloque del Caddyfile para el
# subdominio nuevo y jail de fail2ban con maxretry=30 -- ver los snippets en el
# README/comentarios del proyecto.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

if [ ! -f ./secrets/mcp-token.env ]; then
  echo "Falta secrets/mcp-token.env -- copia secrets/mcp-token.env.example y rellena un token aleatorio." >&2
  exit 1
fi

mkdir -p "$APPDATA_ROOT"

# Contexto de build: raíz del proyecto (necesita copiar db/ además de mcp/).
podman build -t "$IMAGE_NAME" -f mcp/Dockerfile .

podman run -d --name "$CONTAINER_NAME" --replace \
  --restart=always \
  --env-file ./secrets/mcp-token.env \
  -e SHOPPING_LIST_DB="$SHOPPING_LIST_DB" \
  -e WORKFLOWS_DEPLOYMENT_NAME="$WORKFLOWS_DEPLOYMENT_NAME" \
  -p "127.0.0.1:$PORT:8000" \
  -v "$APPDATA_ROOT":/appdata \
  "$IMAGE_NAME"

echo
echo "Desplegado. Comprueba con: podman logs -f $CONTAINER_NAME"
echo "Prueba desde el propio servidor (el MCP exige el token):"
echo "  curl -s -H \"Authorization: Bearer <token>\" http://127.0.0.1:$PORT/mcp"
