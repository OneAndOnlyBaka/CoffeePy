#!/bin/bash
# Restarts the kiosk session and records, every 3s, how far it got - until it has been up for a
# while. cog (kiosk/cog-session): the browser's processes, whether the page has loaded (the
# session's log) and how old the newest poll is (data/storage/kiosk-heartbeat). Chromium (a
# device without cog): the screen's brightness - black (X starting), white (Chromium up, nothing
# painted), then the kiosk (~0.81).
#
#   startup-timeline.sh [SECONDS]     default 90
set -euo pipefail

"$(dirname "$0")/pi.sh" "bash -s ${1:-90}" <<'SH'
T0=$(date +%s)
sudo systemctl restart getty@tty1
if command -v cog >/dev/null; then
	LOG=/run/user/$(id -u)/kiosk.log
	while [ $(( $(date +%s) - T0 )) -lt "$1" ]; do
		sleep 3
		beat=$(( $(date +%s) - $(stat -c %Y /opt/coffeepy/data/storage/kiosk-heartbeat 2>/dev/null || echo 0) ))
		loads=$(grep -c "Loaded successfully" "$LOG" 2>/dev/null || true)
		printf 't=%3ss  cog=%s web=%s  loaded=%s  last poll %ss ago  load=%s\n' "$(( $(date +%s) - T0 ))" \
			"$(pgrep -c -x cog || true)" "$(pgrep -c -x WPEWebProcess || true)" "$loads" "$beat" "$(cut -d' ' -f1 /proc/loadavg)"
	done
	exit 0
fi
command -v xwd >/dev/null || sudo apt-get install -y --no-install-recommends x11-apps imagemagick >/dev/null 2>&1
while [ $(( $(date +%s) - T0 )) -lt "$1" ]; do
	sleep 3
	b=$(DISPLAY=:0 timeout 3 xwd -root -silent 2>/dev/null | convert xwd:- -format "%[fx:mean]" info: 2>/dev/null || echo n/a)
	printf 't=%3ss  chromium procs=%2s  brightness=%s  load=%s\n' "$(( $(date +%s) - T0 ))" \
		"$(pgrep -c -f chromium/chromium || true)" "$b" "$(cut -d' ' -f1 /proc/loadavg)"
done
SH
