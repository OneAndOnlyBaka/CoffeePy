#!/bin/bash
# Inspects the kiosk's real browser: cog (WPE WebKit) through WebDriver (wd.py), or - on a device
# that still runs the Chromium session - Chromium through the DevTools protocol (cdp.py).
#
#   kiosk-browser.sh on              cog: the kiosk session stopped, WPEWebDriver in its place and a
#                                    session that starts cog on the display (same flags, + --automation);
#                                    Chromium: restart the kiosk with --remote-debugging-port=9222
#   kiosk-browser.sh eval 'JS'       the expression's value (both)
#   kiosk-browser.sh shot OUT.png    WebDriver's screenshot of the page (cog; screenshot.sh takes the
#                                    display itself, for both)
#   kiosk-browser.sh cookie          the kiosk's cookies, sessionid included (both)
#   kiosk-browser.sh errors|taps ... cdp.py's (Chromium only, see its docstring)
#   kiosk-browser.sh off             back to the installed kiosk session
#
# ALWAYS finish with "off": WebDriver and the debug port are remote controls for the browser, and
# with cog the kiosk session itself (and the watchdog, which would restart it) is stopped while on.
# on/off restart the browser - the screen is dark for a few seconds (cog) or up to a minute (Chromium).
set -euo pipefail

DIR="$(dirname "$0")"
PI="$DIR/pi.sh"
. "$(git -C "$DIR" rev-parse --show-toplevel)/.device/pi.env"
cmd="${1:?usage: kiosk-browser.sh on|off|eval|shot|errors|taps|cookie}"
shift

if "$PI" 'command -v cog >/dev/null'; then
	case "$cmd" in
		on)
			scp -q "$DIR/wd.py" "$PI_USER@$PI_HOST:/tmp/wd.py"
			"$PI" 'sudo systemctl stop coffeepy-watchdog.timer getty@tty1; sudo rm -f /run/coffeepy-wd-session
				sudo systemctl stop wpedriver 2>/dev/null; sudo systemctl reset-failed wpedriver 2>/dev/null || true
				sudo systemd-run --quiet --unit=wpedriver --uid="$(id -u)" --gid="$(id -g)" \
					-p SupplementaryGroups="video render input" -E XDG_RUNTIME_DIR="/run/user/$(id -u)" -E HOME="$HOME" \
					WPEWebDriver --port=4444 --replace-on-new-session
				sleep 2; sudo python3 -I /tmp/wd.py navigator.userAgent'
			;;
		off)
			"$PI" 'sudo systemctl stop wpedriver 2>/dev/null || true; sudo rm -f /run/coffeepy-wd-session; sleep 1
				sudo systemctl start getty@tty1 coffeepy-watchdog.timer; echo "WebDriver off, kiosk session back"'
			;;
		eval)
			scp -q "$DIR/wd.py" "$PI_USER@$PI_HOST:/tmp/wd.py"
			"$PI" "sudo python3 -I /tmp/wd.py $(printf '%q' "$1")"
			;;
		cookie)
			scp -q "$DIR/wd.py" "$PI_USER@$PI_HOST:/tmp/wd.py"
			"$PI" 'sudo python3 -I /tmp/wd.py cookie'
			;;
		shot)
			scp -q "$DIR/wd.py" "$PI_USER@$PI_HOST:/tmp/wd.py"
			"$PI" 'sudo python3 -I /tmp/wd.py shot /tmp/wd-shot.png >/dev/null'
			scp -q "$PI_USER@$PI_HOST:/tmp/wd-shot.png" "$1" && echo "saved $1"
			;;
		*)
			echo "$cmd: Chromium only (cdp.py) - this device runs cog; eval, shot and cookie work here" >&2
			exit 2
			;;
	esac
	exit 0
fi

case "$cmd" in
	on)
		"$PI" 'grep -q remote-debugging-port ~/.config/openbox/autostart ||
			sed -i "s|        --disable-sync|        --disable-sync --remote-debugging-port=9222|" ~/.config/openbox/autostart
			sudo systemctl restart getty@tty1
			for i in $(seq 90); do curl -s localhost:9222/json 2>/dev/null | grep -q "localhost:8443" && break; sleep 2; done
			sleep 5; curl -s localhost:9222/json/version | grep -o "\"Browser\": *\"[^\"]*\""'
		;;
	off)
		"$PI" 'install -m 0755 ~/CoffeePy/install/kiosk/openbox-autostart ~/.config/openbox/autostart
			sudo systemctl restart getty@tty1; echo "debug port off, kiosk restarting"'
		;;
	*)
		scp -q "$DIR/cdp.py" "$PI_USER@$PI_HOST:/tmp/cdp.py"
		"$PI" "python3 -I /tmp/cdp.py $cmd $(printf '%q ' "$@")"
		;;
esac
