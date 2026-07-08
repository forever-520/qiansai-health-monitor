#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ENV_FILE="${RK_STACK_ENV:-$ROOT/rk_stack.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

WEB_USER="${WEB_USER:-admin}"
WEB_PASS="${WEB_PASS:-}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8081}"

DATA_MODE="${DATA_MODE:-gateway}"
DATA_HTTP_URL="${DATA_HTTP_URL:-http://127.0.0.1:8000}"
DATA_WS_URL="${DATA_WS_URL:-ws://127.0.0.1:8001/ws}"

RK3576_SLAVE_HOST="${RK3576_SLAVE_HOST:-rk3576-slave.local}"
RK3576_SLAVE_PORT="${RK3576_SLAVE_PORT:-9001}"
REMOTE_RADAR_URL="${REMOTE_RADAR_URL:-}"
REMOTE_CAMERA_CAPTURE_URL="${REMOTE_CAMERA_CAPTURE_URL:-}"
GATEWAY_HTTP_PORT="${GATEWAY_HTTP_PORT:-8000}"
GATEWAY_WS_PORT="${GATEWAY_WS_PORT:-8001}"
GATEWAY_RATE="${GATEWAY_RATE:-20.0}"
CAMERA_PROXY_TIMEOUT="${CAMERA_PROXY_TIMEOUT:-40.0}"
CAMERA_JPEG_MAX_SIDE="${CAMERA_JPEG_MAX_SIDE:-960}"
CAMERA_JPEG_QUALITY="${CAMERA_JPEG_QUALITY:-75}"
ENABLE_CLOUDFLARED="${ENABLE_CLOUDFLARED:-1}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-}"

export WEB_USER WEB_PASS HOST PORT DATA_MODE DATA_HTTP_URL DATA_WS_URL
export RK3576_SLAVE_HOST RK3576_SLAVE_PORT REMOTE_RADAR_URL REMOTE_CAMERA_CAPTURE_URL
export GATEWAY_HTTP_PORT GATEWAY_WS_PORT GATEWAY_RATE
export CAMERA_PROXY_TIMEOUT CAMERA_JPEG_MAX_SIDE CAMERA_JPEG_QUALITY

mkdir -p "$ROOT/logs"

python3 "$ROOT/rk3576_rk_gateway.py" \
  --rk3576_slave-host "$RK3576_SLAVE_HOST" \
  --rk3576_slave-port "$RK3576_SLAVE_PORT" \
  --remote-url "$REMOTE_RADAR_URL" \
  --remote-camera-url "$REMOTE_CAMERA_CAPTURE_URL" \
  --http-port "$GATEWAY_HTTP_PORT" \
  --ws-port "$GATEWAY_WS_PORT" \
  --rate "$GATEWAY_RATE" \
  --ui-dir "$ROOT/ui" >"$ROOT/logs/gateway.log" 2>&1 &
GATEWAY_PID="$!"
CLOUDFLARED_PID=""
TUNNEL_URL_PID=""

run_cloudflared_loop() {
  while :; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting cloudflared" >>"$ROOT/logs/cloudflared.out"
    "$CLOUDFLARED_BIN" tunnel --url "http://127.0.0.1:$PORT" \
      --no-autoupdate --protocol http2 >>"$ROOT/logs/cloudflared.out" 2>>"$ROOT/logs/cloudflared.err" || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] cloudflared exited; retry in 10s" >>"$ROOT/logs/cloudflared.err"
    sleep 10
  done
}

update_tunnel_url_loop() {
  while :; do
    URL="$(grep -RhoE 'https://[^ ]+trycloudflare.com' "$ROOT"/logs/cloudflared.* 2>/dev/null | tail -1 || true)"
    if [ -n "$URL" ]; then
      printf '%s\n' "$URL" >"$ROOT/current_tunnel_url.txt"
      printf '%s\n' "$URL" >"$ROOT/ui/current_tunnel_url.txt"
    else
      printf 'waiting for cloudflared tunnel...\n' >"$ROOT/current_tunnel_url.txt"
      printf 'waiting for cloudflared tunnel...\n' >"$ROOT/ui/current_tunnel_url.txt"
    fi
    sleep 5
  done
}

if [ "$ENABLE_CLOUDFLARED" = "1" ]; then
  if [ -z "$CLOUDFLARED_BIN" ]; then
    if command -v cloudflared >/dev/null 2>&1; then
      CLOUDFLARED_BIN="$(command -v cloudflared)"
    elif [ -x "$HOME/bin/cloudflared" ]; then
      CLOUDFLARED_BIN="$HOME/bin/cloudflared"
    fi
  fi

  if [ -n "$CLOUDFLARED_BIN" ] && [ -x "$CLOUDFLARED_BIN" ]; then
    update_tunnel_url_loop &
    TUNNEL_URL_PID="$!"
    run_cloudflared_loop &
    CLOUDFLARED_PID="$!"
  else
    echo "cloudflared not found; external tunnel disabled" >>"$ROOT/logs/cloudflared.err"
  fi
fi

cleanup() {
  kill "$GATEWAY_PID" 2>/dev/null || true
  if [ -n "$CLOUDFLARED_PID" ]; then
    kill "$CLOUDFLARED_PID" 2>/dev/null || true
  fi
  if [ -n "$TUNNEL_URL_PID" ]; then
    kill "$TUNNEL_URL_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

sleep 1
exec python3 "$ROOT/server.py"
