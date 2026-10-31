#!/bin/bash
# Hands a bundle from ~/bundles on the Pi to the update watcher and waits until it is done
# (or failed / rolled back), printing the state every 30s and the update's log at the end.
#
#   apply.sh v2.0.1             ~/bundles/v2.0.1.tar
#   apply.sh --no-wait v2.0.1   just drop it in (power-cut tests: arm powercut.sh first)
#   apply.sh --follow v2.0.1    drop nothing, only wait - for the retry after a power cut
#
# Drops the file into data/updates/incoming/ - the same place the dashboard's upload writes
# to. Test the upload itself once per run too (see SKILL.md).
set -euo pipefail

WAIT=1 DROP=1
case "${1:-}" in --no-wait) WAIT=0; shift ;; --follow) DROP=0; shift ;; esac
NAME="${1:?usage: apply.sh [--no-wait] NAME}"
PI="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.claude/skills/pi-device/scripts/pi.sh"
U=/opt/coffeepy/data/updates

# A copy, so the bundle survives the watcher deleting it - the next attempt or run needs it.
[ "$DROP" = 1 ] && "$PI" "set -e; sudo mkdir -p $U/incoming
sudo cp ~/bundles/$NAME.tar $U/incoming/.$NAME.copy && sudo mv $U/incoming/.$NAME.copy $U/incoming/$NAME.tar
echo \"\$(date +%T) dropped $NAME.tar\""
[ "$WAIT" = 1 ] || exit 0

state() {
	"$PI" "sudo python3 -c 'import json; d=json.load(open(\"$U/state.json\")); print(d.get(\"bundle\"), d[\"state\"], d.get(\"step\") or \"\", d.get(\"message\", \"\")[:90])'" 2>/dev/null
}
[ "$DROP" = 1 ] && sleep 20
while :; do
	s="$(state || true)"
	echo "$(date +%T) $s"
	# rolled_back is final - except after a power cut, when the same bundle "runs again".
	case "$s" in *"runs again"*) ;; "$NAME.tar done"*|"$NAME.tar failed"*|"$NAME.tar rolled_back"*) break ;; esac
	sleep 30
done
"$PI" "sudo tail -30 $U/status/$NAME.tar.log"
