#!/usr/bin/env bash
# Run once on the server with sudo: sets up the systemd service and the nginx site of the review app.
#   sudo bash {{TARGET_DIR}}/server/install.sh
# Touches only its own files: /etc/systemd/system/{{SERVICE}}.service, /etc/nginx/sites-available/{{DOMAIN}} and
# the link in sites-enabled. nginx is reloaded only if "nginx -t" accepts the whole configuration.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SITE={{DOMAIN}}
SERVICE={{SERVICE}}
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo."; exit 1; }

install -m 644 "$HERE/review.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE.service"
systemctl restart "$SERVICE.service"

TARGET="/etc/nginx/sites-available/$SITE"
if [ -e "$TARGET" ] && grep -q "managed by Certbot" "$TARGET"; then
  echo "$TARGET was already extended by certbot (HTTPS) – left unchanged."
else
  install -m 644 "$HERE/nginx-site.conf" "$TARGET"
fi
ln -sfn "$TARGET" "/etc/nginx/sites-enabled/$SITE"
if ! nginx -t; then
  rm -f "/etc/nginx/sites-enabled/$SITE"
  echo "nginx -t reports an error – site disabled again, the running nginx is unchanged."
  exit 1
fi
systemctl reload nginx
sleep 1
systemctl --no-pager --lines=3 status "$SERVICE.service" | head -n 5
echo "Done: http://$SITE/ -> 127.0.0.1:{{PORT}}"
