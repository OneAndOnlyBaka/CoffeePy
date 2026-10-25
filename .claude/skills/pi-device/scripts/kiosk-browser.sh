#!/bin/bash
# Inspects the kiosk's real Chromium through the DevTools protocol.
#
#   kiosk-browser.sh on              restart the kiosk with --remote-debugging-port=9222
#   kiosk-browser.sh errors|eval|taps|egg|cookie ...   run cdp.py on the Pi (see its docstring)
#   kiosk-browser.sh off             restore the installed autostart and restart the kiosk
#
# ALWAYS finish with "off": the debug port is a remote-control socket for the browser, and
# the installed autostart file is the one the next install.sh run expects to find.
# on/off restart the X session - the screen goes black, then white, for up to a minute.
set -euo pipefail

DIR="$(dirname "$0")"
PI="$DIR/pi.sh"
cmd="${1:?usage: kiosk-browser.sh on|off|errors|eval|taps|egg|cookie}"
shift

case "$cmd" in
	on)
		"$PI" 'grep -q remote-debugging-port ~/.config/openbox/autostart ||
			sed -i "s|        --disable-sync|        --disable-sync --remote-debugging-port=9222|" ~/.config/openbox/autostart
			sudo systemctl restart getty@tty1
			for i in $(seq 90); do curl -s localhost:9222/json 2>/dev/null | grep -q "localhost:8000" && break; sleep 2; done
			sleep 5; curl -s localhost:9222/json/version | grep -o "\"Browser\": *\"[^\"]*\""'
		;;
	off)
		"$PI" 'install -m 0755 ~/CoffeePy/install/kiosk/openbox-autostart ~/.config/openbox/autostart
			sudo systemctl restart getty@tty1; echo "debug port off, kiosk restarting"'
		;;
	*)
		. "$(git -C "$DIR" rev-parse --show-toplevel)/.device/pi.env"
		scp -q "$DIR/cdp.py" "$PI_USER@$PI_HOST:/tmp/cdp.py"
		"$PI" "python3 -I /tmp/cdp.py $cmd $(printf '%q ' "$@")"
		;;
esac
