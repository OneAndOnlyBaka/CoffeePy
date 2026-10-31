#!/bin/bash
# The device-side checks after a hop (SKILL.md "Checks after every hop"), in one go - the data
# itself is fingerprint.py's job. Lines starting with "!!" need a look.
#
#   checks.sh
set -euo pipefail

PI="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.claude/skills/pi-device/scripts/pi.sh"
"$PI" 'U=/opt/coffeepy/data/updates
cd /opt/coffeepy
# The running image's version - the host's copy of the tree is the installer's, not the update's.
echo "version:   $(sudo docker compose exec -T web cat coffeepy-version.json | sed -n "s/.*\"version\": \"\([^\"]*\)\".*/\1/p")"
sudo python3 -c "import json; d=json.load(open(\"$U/state.json\")); print(\"state:    \", d[\"state\"], \"-\", d.get(\"message\"))" 2>/dev/null || echo "state:     (none)"
code=$(curl -s -o /dev/null -w "%{http_code}" --cacert data/tls/ca.crt https://localhost:8443/health/ || true)
[ "$code" = 200 ] && echo "health:    200" || echo "!! health: $code"
failed=$(systemctl --failed --no-legend --plain | cut -d" " -f1 | tr "\n" " ")
[ -z "$failed" ] && echo "units:     none failed" || echo "!! failed units: $failed"
echo "installed: $(systemctl list-unit-files "coffeepy*" --no-legend | awk "{print \$1\"=\"\$2}" | tr "\n" " ")"
bad=$(sudo docker ps -a --format "{{.Names}} {{.Status}}" | grep -vE " Up |coffeepy-migrate-1 Exited \(0\)" || true)
[ -z "$bad" ] && echo "containers: all up" || echo "!! containers: $bad"
restarts=$(sudo docker inspect -f "{{.Name}}={{.RestartCount}}" $(sudo docker ps -q) | grep -v "=0$" || true)
[ -z "$restarts" ] || echo "!! restarted: $restarts"
extra=$(sudo ls -A $U | grep -vxE "incoming|installed.json|pre-update|state.json|status|system.json|os-packages.txt|os-installed.json" | tr "\n" " " || true)
[ -z "$extra" ] && echo "leftovers: none in data/updates" || echo "!! left in data/updates: $extra"
[ -z "$(sudo ls -A $U/incoming 2>/dev/null)" ] || echo "!! incoming not empty: $(sudo ls -A $U/incoming | tr "\n" " ")"
tags=$(sudo docker images --format "{{.Repository}}:{{.Tag}}" | grep coffeepy-previous || true)
[ -z "$tags" ] && echo "tags:      no coffeepy-previous-*" || echo "!! previous tags: $tags"
[ -e data/maintenance/status.json ] && echo "!! maintenance status still there"
newest=$(sudo ls -t $U/pre-update 2>/dev/null | head -1)
case "$newest" in
	"") echo "dump:      none yet" ;;
	*.sql.gz) sudo zcat "$U/pre-update/$newest" | tail -c 512 | grep -q "dump complete" \
		&& echo "dump:      $newest complete" || echo "!! dump incomplete: $newest" ;;
	*) echo "dump:      $newest (custom format, an older watcher)" ;;
esac
echo "images:    $(sudo docker images --format "{{.Repository}}:{{.Tag}}={{.ID}}" | grep -v "<none>" | tr "\n" " ")"
echo "disk:      $(df -h / | awk "NR==2 {print \$3\" used, \"\$4\" free\"}")"
errors=$(sudo journalctl -p err -b --no-pager -q | grep -v "kernel:" | tail -5 || true)
[ -z "$errors" ] || { echo "!! journal errors this boot (last 5):"; echo "$errors"; }
dmesg_err=$(sudo dmesg -l err,crit,alert,emerg 2>/dev/null | tail -3 || true)
[ -z "$dmesg_err" ] || { echo "!! dmesg errors (last 3):"; echo "$dmesg_err"; }
true'
