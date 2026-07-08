#!/usr/bin/env sh
set -eu

# Start a static Web server from the bundled ui directory.
PORT="${PORT:-8081}"
HOST="${HOST:-0.0.0.0}"

cd "$(dirname "$0")"
exec python3 server.py
