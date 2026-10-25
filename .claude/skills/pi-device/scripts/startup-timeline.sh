#!/bin/bash
# Restarts the kiosk session and records what the screen shows, every 3s, until the kiosk
# page has been up for a while: black (X starting), white (Chromium up, nothing painted),
# then the kiosk (beige, brightness ~0.81).
#
#   startup-timeline.sh [SECONDS]     default 90
set -euo pipefail

"$(dirname "$0")/pi.sh" "bash -s ${1:-90}" <<'EOF'
command -v xwd >/dev/null || sudo apt-get install -y --no-install-recommends x11-apps imagemagick >/dev/null 2>&1
sudo systemctl restart getty@tty1
T0=$(date +%s)
while [ $(( $(date +%s) - T0 )) -lt "$1" ]; do
	sleep 3
	b=$(DISPLAY=:0 timeout 3 xwd -root -silent 2>/dev/null | convert xwd:- -format "%[fx:mean]" info: 2>/dev/null || echo n/a)
	printf 't=%3ss  chromium procs=%2s  brightness=%s  load=%s\n' "$(( $(date +%s) - T0 ))" \
		"$(pgrep -c -f chromium/chromium || true)" "$b" "$(cut -d' ' -f1 /proc/loadavg)"
done
EOF
