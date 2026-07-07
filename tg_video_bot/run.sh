#!/usr/bin/env bash
# Home Assistant add-on entrypoint: reads /data/options.json, starts the local
# telegram-bot-api server, then launches the bot. Both processes run in this
# single container. On the non-HA (docker-compose) path this file is NOT used —
# there the two run as separate services and bot.py reads plain env vars.
set -euo pipefail

OPTIONS=/data/options.json

# --- read add-on options -----------------------------------------------------
BOT_TOKEN="$(jq -r '.bot_token // ""' "$OPTIONS")"
API_ID="$(jq -r '.api_id // 0' "$OPTIONS")"
API_HASH="$(jq -r '.api_hash // ""' "$OPTIONS")"
DEFAULT_FORMAT="$(jq -r '.default_format // "best"' "$OPTIONS")"
COOKIES_PATH="$(jq -r '.cookies_path // ""' "$OPTIONS")"
# array -> comma separated
ALLOWED_USER_IDS="$(jq -r '(.allowed_user_ids // []) | map(tostring) | join(",")' "$OPTIONS")"

if [ -z "$BOT_TOKEN" ]; then
  echo "[run.sh] FATAL: bot_token is not set in add-on options" >&2
  exit 1
fi
if [ "$API_ID" = "0" ] || [ -z "$API_HASH" ]; then
  echo "[run.sh] FATAL: api_id / api_hash are required (get them at https://my.telegram.org)" >&2
  exit 1
fi

# --- start local Bot API server ---------------------------------------------
BOTAPI_DIR=/data/botapi
mkdir -p "$BOTAPI_DIR/temp" /data/tmp

echo "[run.sh] starting telegram-bot-api (local mode) on :8081"
telegram-bot-api \
  --local \
  --api-id="$API_ID" \
  --api-hash="$API_HASH" \
  --http-port=8081 \
  --dir="$BOTAPI_DIR" \
  --temp-dir="$BOTAPI_DIR/temp" &
BOTAPI_PID=$!

# stop the whole container if either process dies
trap 'kill "$BOTAPI_PID" 2>/dev/null || true' EXIT

echo "[run.sh] waiting for Bot API server ..."
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:8081/" 2>/dev/null; then break; fi
  # a running server answers 404 (not a connection error) -> also good enough
  if curl -sS -o /dev/null "http://127.0.0.1:8081/" 2>/dev/null; then break; fi
  if ! kill -0 "$BOTAPI_PID" 2>/dev/null; then
    echo "[run.sh] FATAL: telegram-bot-api exited early" >&2
    exit 1
  fi
  sleep 1
done

# --- run the bot -------------------------------------------------------------
export BOT_TOKEN API_ID API_HASH DEFAULT_FORMAT COOKIES_PATH ALLOWED_USER_IDS
export BOT_API_BASE_URL="http://127.0.0.1:8081"
export DATA_DIR="/data"

echo "[run.sh] deno: $(deno --version 2>&1 | head -1 || echo 'MISSING — YouTube JS challenges will fail')"
echo "[run.sh] starting bot"
exec python3 /bot.py
