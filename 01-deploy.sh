#!/bin/bash
# Builds the shopping-list MCP server image locally (own Dockerfile in ./mcp, build
# context is the project root because it imports db/ as-is) and deploys it. Published
# only on 127.0.0.1:$PORT on purpose (not on the whole LAN) -- a reverse proxy in front
# (e.g. Caddy with --network host) reaches it just the same over loopback, so this
# service stays protected behind MCP_AUTH_TOKEN without being directly exposed on the
# local network. Meant to be connected as a "Connector" from Mistral AI.
#
# The SQLite file lives on $APPDATA_ROOT (its own appdata volume), mounted at /appdata
# inside the container -- survives container rebuilds.
#
# Separately (not included here, depends on your own infrastructure): a reverse proxy
# with HTTPS in front of the published port and, if you expose it to the internet, a
# fail2ban jail over its failed authentication attempts.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

if [ ! -f ./secrets/mcp-token.env ]; then
  echo "Missing secrets/mcp-token.env -- copy secrets/mcp-token.env.example and fill in a random token." >&2
  exit 1
fi

mkdir -p "$APPDATA_ROOT"

# Build context: project root (needs to copy db/ as well as mcp/).
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
echo "Deployed. Check with: podman logs -f $CONTAINER_NAME"
echo "Test from the server itself (the MCP requires the token):"
echo "  curl -s -H \"Authorization: Bearer <token>\" http://127.0.0.1:$PORT/mcp"
