#!/usr/bin/env bash
# Discover the OpenClaw CLI surface, then install + enable the HTTP plugin
# and configure it to allow plain HTTP (no TLS) so the bot can call the
# pipeline control server at http://<sparky>:8765.
#
# Run from the repo root on the host where clipCowboy_bot is running:
#   ./scripts/enable-http-plugin.sh
#
# It's verbose on purpose. The plugin CLI in 2026.5.2 isn't documented in
# this repo, so the script probes the available subcommands, prints what
# it finds, then tries the most likely install/enable invocations in
# order, stopping at the first success. If everything fails, the output
# tells us exactly what surface 2026.5.2 actually exposes and we can
# pick the right commands from there.

set -uo pipefail

CONTAINER="${CONTAINER:-clipCowboy_bot}"
PIPELINE_URL="${PIPELINE_CONTROL_URL:-http://192.168.1.38:8765}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Container '$CONTAINER' is not running. Start it first: docker compose up -d $CONTAINER" >&2
  exit 1
fi

xc() { echo "+ $*"; docker exec "$CONTAINER" "$@" 2>&1; }
xc_ok() { docker exec "$CONTAINER" "$@" >/dev/null 2>&1; }

hdr() { printf '\n=== %s ===\n' "$1"; }

hdr "openclaw top-level help"
xc openclaw --help || true

hdr "candidate plugin/tool subcommand help (any that exist)"
for sub in plugin plugins tool tools extension extensions module modules; do
  if xc_ok openclaw "$sub" --help; then
    echo "--- openclaw $sub --help ---"
    xc openclaw "$sub" --help
  fi
done

hdr "current plugin list (best-effort, ignore failures)"
xc openclaw plugin list      || true
xc openclaw plugins list     || true
xc openclaw plugins          || true
xc openclaw tool list        || true
xc openclaw tools list       || true

hdr "current openclaw config (filtered for http/tools/plugins)"
xc openclaw config get 2>&1 | grep -iE 'http|tool|plugin|allow|insecure|tls|ssl|verify' || true

hdr "attempting install (will skip on first success)"
INSTALLED=""
for cmd in \
  "openclaw plugin install http" \
  "openclaw plugins install http" \
  "openclaw tool install http" \
  "openclaw extension install http" \
  "openclaw plugin add http" \
  "openclaw plugins add http"; do
  echo "+ $cmd"
  if docker exec "$CONTAINER" $cmd 2>&1; then
    INSTALLED="$cmd"
    echo "✓ install command accepted: $cmd"
    break
  fi
done
[[ -z "$INSTALLED" ]] && echo "(no install command accepted — plugin may already be present, or CLI uses a different verb)"

hdr "attempting enable (will skip on first success)"
ENABLED=""
for cmd in \
  "openclaw plugin enable http" \
  "openclaw plugins enable http" \
  "openclaw tool enable http" \
  "openclaw extension enable http"; do
  echo "+ $cmd"
  if docker exec "$CONTAINER" $cmd 2>&1; then
    ENABLED="$cmd"
    echo "✓ enable command accepted: $cmd"
    break
  fi
done

# Fallback: many tools store enable state in config. Try common config paths.
if [[ -z "$ENABLED" ]]; then
  echo "(no enable subcommand accepted — falling back to config set on common paths)"
  for path in plugins.http.enabled tools.http.enabled extensions.http.enabled http.enabled; do
    echo "+ openclaw config set $path true"
    if docker exec "$CONTAINER" openclaw config set "$path" true --strict-json 2>&1; then
      ENABLED="config set $path true"
      echo "✓ config set accepted: $path"
      break
    fi
  done
fi

hdr "allow plain HTTP (no TLS) — try common config knobs"
# OpenClaw's HTTP plugin (per the user) refused plain HTTP after the bot
# moved to a remote host. Try the most likely opt-out flags. Failures are
# fine — we want to find which one sticks, not run them all.
for path_val in \
  "plugins.http.allowInsecure true" \
  "plugins.http.allow_insecure true" \
  "plugins.http.allowHttp true" \
  "plugins.http.requireTls false" \
  "plugins.http.tlsRequired false" \
  "tools.http.allowInsecure true" \
  "tools.http.requireTls false" \
  "http.allowInsecure true" \
  "http.requireTls false"; do
  set -- $path_val
  echo "+ openclaw config set $1 $2"
  docker exec "$CONTAINER" openclaw config set "$1" "$2" --strict-json 2>&1 || true
done

hdr "validate config"
xc openclaw config validate || true

hdr "show http-related config after changes"
xc openclaw config get 2>&1 | grep -iE 'http|tool|plugin|allow|insecure|tls|ssl|verify' || true

hdr "restart container so plugin loads"
docker restart "$CONTAINER" >/dev/null
sleep 3

hdr "post-restart plugin list (look for 'http')"
docker compose logs --tail=50 "$CONTAINER" 2>&1 | grep -iE 'plugins?:|http' || true

cat <<EOF

Done. If the post-restart log line listing plugins now contains 'http',
the bot has the capability and SOUL.md's "POST $PIPELINE_URL/reset" should
work. Test in Discord with: "factory reset" or "pipeline status".

If 'http' still isn't in the plugin list, paste the "openclaw top-level
help" and "candidate plugin/tool subcommand help" sections above and we
can pick the exact verb 2026.5.2 wants.
EOF
