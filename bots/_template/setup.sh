#!/bin/bash
# Run once after first start to configure model and presence.
# Usage: BOT_CONTAINER=footage_mybot ./bots/_template/setup.sh
#
# Supported backends:
#   google/gemini-flash-lite-latest   (free tier, fast)
#   google/gemini-flash-latest        (free tier, smarter)
#   ollama/gemma4:e4b                 (local)
#   ollama/<any-model>                (local)

BOT_CONTAINER=${BOT_CONTAINER:?Usage: BOT_CONTAINER=footage_mybot ./setup.sh}
PRIMARY=${PRIMARY:-google/gemini-flash-lite-latest}
FALLBACK=${FALLBACK:-google/gemini-flash-latest}

set -e

echo "Configuring $BOT_CONTAINER..."

docker exec "$BOT_CONTAINER" openclaw config set agents.defaults.model.primary "$PRIMARY"
docker exec "$BOT_CONTAINER" openclaw config set agents.defaults.model.fallbacks "[\"$FALLBACK\"]"
docker exec "$BOT_CONTAINER" openclaw config set agents.defaults.heartbeat.every "0m"
docker exec "$BOT_CONTAINER" openclaw config set channels.discord.status online
docker exec "$BOT_CONTAINER" openclaw config set channels.discord.autoPresence.enabled true
docker exec "$BOT_CONTAINER" openclaw config set channels.discord.autoPresence.intervalMs 30000

echo "Done. Verify with:"
echo "  docker exec $BOT_CONTAINER openclaw config get agents.defaults.model"
