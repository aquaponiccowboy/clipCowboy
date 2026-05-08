#!/usr/bin/env bash
# Idempotently apply OpenClaw bot configuration.
#
# Reads values from environment (.env via direnv, or shell). Edit .env to
# override defaults. Run after deploys, image upgrades, or whenever the bot's
# config drifts (e.g. apiKey reverts to its placeholder, providers go missing).
#
# Why this exists: OpenClaw rewrites parts of openclaw.json on every boot, and
# version upgrades can introduce required fields the old config didn't have.
# Re-running this script restores known-good config in seconds.

set -euo pipefail

# Load values from .env if it's next to docker-compose.yml — otherwise the
# script ran without the env exported and would fail later on missing
# DISCORD_GUILD_ID / DISCORD_CHANNEL_ID even when they're set in .env.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

CONTAINER="${CONTAINER:-clipCowboy_bot}"

: "${OLLAMA_HOST:=http://ollama.truck:11434}"
: "${OLLAMA_API_KEY:=ollama-local}"
: "${DISCORD_GUILD_ID:=}"
: "${DISCORD_CHANNEL_ID:=}"

if [[ -z "$DISCORD_GUILD_ID" || -z "$DISCORD_CHANNEL_ID" ]]; then
  echo "DISCORD_GUILD_ID and DISCORD_CHANNEL_ID must be set in .env" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Container '$CONTAINER' is not running. Start it first: docker compose up -d $CONTAINER" >&2
  exit 1
fi

# OpenClaw 2026.5.x split discord out of the stock plugin bundle. Install the
# external plugin idempotently — `plugins install` is a no-op if the package
# is already linked under /home/node/.openclaw/npm/node_modules/@openclaw/.
echo "Installing @openclaw/discord (idempotent)..."
docker exec "$CONTAINER" openclaw plugins install @openclaw/discord >/dev/null 2>&1 || true

set_json() {
  # OpenClaw 2026.5.7+ refuses object writes that drop existing fields unless
  # the caller passes --merge or --replace. Trailing args after value are
  # forwarded so each caller can pick the right mode for its payload.
  local path="$1" value="$2"
  shift 2
  docker exec "$CONTAINER" openclaw config set "$path" "$value" --strict-json "$@"
}

# Discord plugin — explicit enable. In 2026.5.7 the default-enable rule
# flipped, so an absent entry means the plugin doesn't load.
set_json plugins.entries.discord.enabled true

# Ollama provider — merge so OpenClaw's auto-managed fields (e.g.
# timeoutSeconds added on first boot) survive the rewrite. Our payload
# defines baseUrl/apiKey/models authoritatively; everything else stays.
set_json models.providers.ollama \
  "$(printf '{"baseUrl":"%s","apiKey":"%s","models":[]}' "$OLLAMA_HOST" "$OLLAMA_API_KEY")" \
  --merge

# Discord scope — replace so only this guild + channel are present.
# groupPolicy=allowlist means any other guild the bot finds itself in is
# ignored.
set_json channels.discord.guilds \
  "$(printf '{"%s":{"slug":"main","channels":{"%s":{"enabled":true,"requireMention":false}}}}' \
    "$DISCORD_GUILD_ID" "$DISCORD_CHANNEL_ID")" \
  --replace

docker exec "$CONTAINER" openclaw config set channels.discord.groupPolicy allowlist

# web_fetch — turn it on and route through the bot_proxy sidecar so the SSRF
# guard's RFC 1918 block doesn't stop the bot from reaching the pipeline
# control server at http://192.168.1.38:8765. HTTP_PROXY on clipCowboy_bot
# points at bot_proxy:8888 (see docker-compose.yml + proxy/tinyproxy.conf).
set_json tools.web.fetch.enabled true
set_json tools.web.fetch.useTrustedEnvProxy true

echo
echo "Validating..."
docker exec "$CONTAINER" openclaw config validate

echo
echo "Restarting $CONTAINER..."
docker restart "$CONTAINER" >/dev/null

echo
echo "Done. Tail logs with:  docker logs -f --since 30s $CONTAINER"
