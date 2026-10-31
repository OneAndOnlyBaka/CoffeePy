#!/bin/bash
# Arms a power cut on the test Pi: a hard reset (sysrq b - no sync, no unmount, as close to
# pulling the plug as software gets) the moment the update journal reaches PHASE, plus an
# optional delay. Arm it before apply.sh --no-wait; it fires once and dies with the reboot.
#
#   powercut.sh loaded 5        5s after the images were loaded (the dump is running)
#   powercut.sh switched 3      new version in place, data saved - the full rollback case
#   powercut.sh switched 40     during the health check / services step
#   powercut.sh --wait          wait for the reboot that follows, then show the recovery
#
# Phases: install/update_watcher.py PHASES. Watchers before the journal have none to wait for.
set -euo pipefail

PI="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.claude/skills/pi-device/scripts/pi.sh"

if [ "${1:-}" = "--wait" ]; then
	boot="$("$PI" 'cat /proc/sys/kernel/random/boot_id')"
	# A single failed ssh is no reboot (the WiFi drops the odd one) - the boot id says.
	until now="$("$PI" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null)" && [ "$now" != "$boot" ]; do sleep 5; done
	echo "$(date +%T) rebooted"
	# The recover unit runs before the stack, so it is through once the boot is.
	until "$PI" 'systemctl is-system-running --wait' 2>/dev/null | grep -qE '^(running|degraded)$'; do sleep 5; done
	"$PI" 'cat /var/tmp/powercut.last 2>/dev/null; sudo journalctl -b -u coffeepy-update-recover --no-pager -o cat | tail -8'
	exit 0
fi

PHASE="${1:?usage: powercut.sh PHASE [DELAY] | --wait}"
DELAY="${2:-0}"
"$PI" "sudo rm -f /var/tmp/powercut.last; sudo systemd-run --quiet --unit=powercut-\$(date +%s) sh -c '
	while ! grep -q \"\\\"phase\\\": \\\"$PHASE\\\"\" /opt/coffeepy/data/updates/journal.json 2>/dev/null; do sleep 0.2; done
	sleep $DELAY
	echo \"cut at $PHASE+${DELAY}s \$(date +%T)\" > /var/tmp/powercut.last; sync /var/tmp/powercut.last
	echo 1 > /proc/sys/kernel/sysrq; echo b > /proc/sysrq-trigger' && echo 'armed: $PHASE + ${DELAY}s'"
