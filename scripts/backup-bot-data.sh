#!/usr/bin/env bash
# Snapshot bots/clipCowboy/data/ to a timestamped tarball. Keeps last 7 days.
#
# Run manually or via cron. Example crontab line (daily at 03:15):
#   15 3 * * *  cd $HOME/clipCowboy && ./scripts/backup-bot-data.sh >> /var/log/clipCowboy-backup.log 2>&1
#
# Override the destination by setting BOT_BACKUP_DIR (default: ~/clipCowboy-backups).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_REL="bots/clipCowboy/data"
SRC_ABS="$REPO_ROOT/$SRC_REL"
DEST="${BOT_BACKUP_DIR:-$HOME/clipCowboy-backups}"
RETENTION_DAYS="${BOT_BACKUP_RETENTION_DAYS:-7}"

if [[ ! -d "$SRC_ABS" ]]; then
  echo "No bot data dir at $SRC_ABS — nothing to back up." >&2
  exit 0
fi

mkdir -p "$DEST"
TS=$(date +%Y%m%d-%H%M%S)
ARCHIVE="$DEST/clipCowboy-data-$TS.tar.gz"

tar -czf "$ARCHIVE" -C "$REPO_ROOT" "$SRC_REL"
echo "Backed up to: $ARCHIVE"

# Rotate
find "$DEST" -maxdepth 1 -name 'clipCowboy-data-*.tar.gz' -mtime +"$RETENTION_DAYS" -delete
