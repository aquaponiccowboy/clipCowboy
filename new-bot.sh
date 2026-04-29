#!/bin/bash
# Scaffold a new bot from the template.
# Usage: ./new-bot.sh <bot-name>
# Example: ./new-bot.sh creative

NAME=$1
if [ -z "$NAME" ]; then
    echo "Usage: ./new-bot.sh <bot-name>"
    exit 1
fi

DEST="bots/$NAME"

if [ -d "$DEST" ]; then
    echo "Error: $DEST already exists."
    exit 1
fi

mkdir -p "$DEST/workspace" "$DEST/data"
cp bots/_template/workspace/* "$DEST/workspace/"
cp bots/_template/setup.sh "$DEST/setup.sh"
chmod +x "$DEST/setup.sh"

# Fix data dir ownership to match OpenClaw container user (node = uid 1000)
sudo chown -R 1000:1000 "$DEST/data" 2>/dev/null || true

echo ""
echo "Created $DEST/"
echo ""
echo "Next steps:"
echo "  1. Edit $DEST/workspace/SOUL.md     — define what the bot does"
echo "  2. Edit $DEST/workspace/IDENTITY.md — name, emoji, vibe"
echo "  3. Add to .env:  BOT_${NAME^^}_TOKEN=<discord-bot-token>"
echo "  4. Add service to docker-compose.yml (copy the commented bot-template block)"
echo "  5. docker compose up -d bot-$NAME"
echo "  6. BOT_CONTAINER=footage_$NAME ./bots/$NAME/setup.sh"
echo ""
echo "Optional: set PRIMARY/FALLBACK model in setup.sh before running it."
echo "  PRIMARY=google/gemini-flash-latest BOT_CONTAINER=footage_$NAME ./bots/$NAME/setup.sh"
