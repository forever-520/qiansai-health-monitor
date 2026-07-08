#!/usr/bin/env sh
set -eu

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="rk_web_ui.service"
ENV_FILE="$APP_DIR/rk_web_ui.env"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo ./install_systemd.sh" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/rk_web_ui.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE"
  echo "Edit WEB_PASS before starting the service." >&2
  exit 1
fi

if ! grep -q '^WEB_PASS=.\+' "$ENV_FILE"; then
  echo "Set WEB_PASS in $ENV_FILE before installing." >&2
  exit 1
fi

if grep -q '^WEB_PASS=change-this-password$' "$ENV_FILE"; then
  echo "Change the default WEB_PASS in $ENV_FILE before installing." >&2
  exit 1
fi

sed "s#WorkingDirectory=.*#WorkingDirectory=$APP_DIR#; s#EnvironmentFile=.*#EnvironmentFile=$ENV_FILE#; s#ExecStart=.*#ExecStart=/usr/bin/python3 $APP_DIR/server.py#" \
  "$APP_DIR/rk_web_ui.service" > "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME"
