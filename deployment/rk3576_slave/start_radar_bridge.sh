#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 /home/cat/radar_serial_bridge.py \
  --serial-port /dev/ttyS10 \
  --baud 115200 \
  --host 0.0.0.0 \
  --port 8000 \
  --camera-device /dev/video11 \
  --camera-width 1280 \
  --camera-height 720 \
  --jpeg-quality 75 \
  --camera-cache-interval 8
