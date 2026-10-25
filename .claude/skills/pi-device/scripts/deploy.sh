#!/bin/bash
# Syncs the working tree and brings the running device up to date with it.
#
#   deploy.sh            app code/templates/static: copy to /opt/coffeepy, rebuild, restart
#   deploy.sh kiosk      also re-install the kiosk session files and restart Chromium
#   deploy.sh STEP...    any install.sh steps, e.g. "packages ap" (runs them with --only)
#
# Templates and static files are baked into the image (collectstatic runs in the
# Dockerfile), so "app" always means a rebuild, never just a restart.
set -euo pipefail

DIR="$(dirname "$0")"
"$DIR/sync.sh"

steps=("app" "$@")
for step in "${steps[@]}"; do
	"$DIR/pi.sh" "cd CoffeePy && sudo ./install/install.sh --only=$step --yes 2>&1 | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | grep -E '^(==|!!)' | grep -v '^=== '" || true
done

"$DIR/pi.sh" 'cd /opt/coffeepy && sudo docker compose build web 2>&1 | tail -1 && sudo docker compose up -d 2>&1 | tail -1
for i in $(seq 60); do curl -sf localhost:8000/health/ >/dev/null && break; sleep 2; done
curl -s -o /dev/null -w "health %{http_code}\n" localhost:8000/health/'

if printf '%s\n' "$@" | grep -qx kiosk; then
	"$DIR/pi.sh" 'sudo systemctl restart getty@tty1 && echo "kiosk session restarted (screen is blank/white for up to a minute)"'
fi
