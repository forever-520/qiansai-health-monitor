#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export RADAR_REMOTE_URL="${RADAR_REMOTE_URL:-http://127.0.0.1:8000/radar/raw}"
export RK3576_SLAVE_CAPTURE_HOST="${RK3576_SLAVE_CAPTURE_HOST:-127.0.0.1}"
export RK3576_SLAVE_CAPTURE_PORT="${RK3576_SLAVE_CAPTURE_PORT:-8000}"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

if [ -S "${XDG_RUNTIME_DIR}/wayland-0" ]; then
  export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
fi

if [ -z "${XAUTHORITY:-}" ]; then
  xauth_file="$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -name '.mutter-Xwaylandauth.*' 2>/dev/null | head -n 1 || true)"
  if [ -n "$xauth_file" ]; then
    export XAUTHORITY="$xauth_file"
  fi
fi

exec python3 main.py
