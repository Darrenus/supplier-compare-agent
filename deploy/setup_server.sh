#!/usr/bin/env bash
# One-time (and idempotent) setup of the Lightsail box (Ubuntu 24.04).
# Run ON THE SERVER from the app directory:  sudo bash deploy/setup_server.sh
#
# Result: gunicorn serves app:app on 127.0.0.1:8080 as a systemd service
# (restarts on crash and on reboot), nginx proxies port 80 to it.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-ubuntu}"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "Missing $APP_DIR/.env (copy .env.example and fill in the gateway key)." >&2
  exit 1
fi

apt-get update -qq
apt-get install -y -qq python3-venv python3-pip nginx

sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" gunicorn

sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__APP_USER__#$APP_USER#g" \
  "$APP_DIR/deploy/supplier-agent.service" > /etc/systemd/system/supplier-agent.service
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/supplier-agent
ln -sf /etc/nginx/sites-available/supplier-agent /etc/nginx/sites-enabled/supplier-agent
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable --now supplier-agent
systemctl restart supplier-agent
nginx -t
systemctl reload nginx

sleep 2
curl -fsS http://127.0.0.1/api/health && echo && echo "Deployed."
