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

set_json() {
  local path="$1" value="$2"
  docker exec "$CONTAINER" openclaw config set "$path" "$value" --strict-json
}

# Ollama provider — top-level authoritative entry. Per-agent models.json
# baseUrl is preserved by merge mode; apiKey is normalized from this entry.
set_json models.providers.ollama \
  "$(printf '{"baseUrl":"%s","apiKey":"%s","models":[]}' "$OLLAMA_HOST" "$OLLAMA_API_KEY")"

# Discord scope — only this guild + channel. groupPolicy=allowlist means
# any other guild the bot finds itself in is ignored.
set_json channels.discord.guilds \
  "$(printf '{"%s":{"slug":"main","channels":{"%s":{"enabled":true,"requireMention":false}}}}' \
    "$DISCORD_GUILD_ID" "$DISCORD_CHANNEL_ID")"

docker exec "$CONTAINER" openclaw config set channels.discord.groupPolicy allowlist

echo
echo "Validating..."
docker exec "$CONTAINER" openclaw config validate

echo
echo "Restarting $CONTAINER..."
docker restart "$CONTAINER" >/dev/null

echo
echo "Done. Tail logs with:  docker logs -f --since 30s $CONTAINER"
