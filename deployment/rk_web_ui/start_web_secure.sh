#!/usr/bin/env sh
set -eu

if [ -z "${WEB_PASS:-}" ]; then
  echo "Set WEB_PASS before starting a public or LAN-shared server." >&2
  echo "Example: WEB_USER=admin WEB_PASS=change-me ./start_web_secure.sh" >&2
  exit 1
fi

PORT="${PORT:-8081}"
HOST="${HOST:-0.0.0.0}"

cd "$(dirname "$0")"
exec python3 server.py
