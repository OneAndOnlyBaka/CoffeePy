#!/bin/bash
# "My coffees" on the device, with real touches (kiosk_slots_test.py): the editor (sort, strength, water,
# cups), ordering the 2-cup slot - the simulator brews both, two orders booked - and the order screen with
# the sorts hidden. Needs the coffee machine simulator (MACHINE_SIMULATE=true on machine-bridge) and logs
# in with synthetic scans of the probe card - ask the user first (SKILL.md). Screenshots: /tmp/slots-shots.
#
#   kiosk-slots-test.sh [ROUNDS]
set -euo pipefail
DIR="$(dirname "$0")"
. "$(git -C "$DIR" rev-parse --show-toplevel)/.device/pi.env"
rounds="${1:-3}"
scp -q "$DIR/kiosk_slots_test.py" "$PI_USER@$PI_HOST:/tmp/slots_device.py"
"$DIR/kiosk-browser.sh" on >/dev/null
trap '"$DIR/django.sh" "$DIR/kiosk_slots_probe.py" down >/dev/null; "$DIR/kiosk-browser.sh" off' EXIT
for _ in $(seq "$rounds"); do
	"$DIR/django.sh" "$DIR/kiosk_slots_probe.py" down >/dev/null 2>&1 || true
	"$DIR/django.sh" "$DIR/kiosk_slots_probe.py" up >/dev/null
	"$DIR/pi.sh" 'sudo python3 -I /tmp/slots_device.py'
	sleep 3
done
