#!/usr/bin/env sh
set -eu

SERVICE_NAME="rk_web_ui.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo ./uninstall_systemd.sh" >&2
  exit 1
fi

systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload
echo "Removed $SERVICE_NAME"
