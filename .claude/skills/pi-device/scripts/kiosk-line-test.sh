#!/bin/bash
# The line for the machine on the device (kiosk_line_test.py): a phone (the portal over HTTPS) and the kiosk (real touches)
# at once - the phone called first, the kiosk member queued behind it and called once the phone's brew is done, a place given
# up, a minute run out. Needs the coffee machine simulator (MACHINE_SIMULATE=true on machine-bridge) and logs in with
# synthetic scans of the probe card - ask the user first (SKILL.md). Screenshots: /tmp/line-shots.
#
#   kiosk-line-test.sh [ROUNDS]
set -euo pipefail
DIR="$(dirname "$0")"
. "$(git -C "$DIR" rev-parse --show-toplevel)/.device/pi.env"
rounds="${1:-3}"
scp -q "$DIR/kiosk_line_test.py" "$DIR/wd.py" "$PI_USER@$PI_HOST:/tmp/"
"$DIR/kiosk-browser.sh" on >/dev/null
trap '"$DIR/django.sh" "$DIR/kiosk_line_probe.py" down >/dev/null; "$DIR/kiosk-browser.sh" off' EXIT
for _ in $(seq "$rounds"); do
	"$DIR/django.sh" "$DIR/kiosk_line_probe.py" down >/dev/null 2>&1 || true
	session="$("$DIR/django.sh" "$DIR/kiosk_line_probe.py" up | sed -n 's/^SESSION //p')"
	"$DIR/pi.sh" "sudo python3 -I /tmp/kiosk_line_test.py $session"
	sleep 3
done
