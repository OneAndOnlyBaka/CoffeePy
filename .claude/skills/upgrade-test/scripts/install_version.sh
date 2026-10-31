#!/bin/bash
# Puts an older release onto the test Pi as a fresh install - the starting point of an upgrade
# test. The device's database and update state are WIPED; data/tls (the CA phones trust) and
# data/storage (device id, backup stick) stay.
#
#   install_version.sh v2.0.0
#
# Then wait for ~/install.out on the Pi to end with the verify step (Monitor), ~25 min.
set -euo pipefail

TAG="${1:?usage: install_version.sh TAG}"
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PI="$ROOT/.claude/skills/pi-device/scripts/pi.sh"
. "$ROOT/.device/pi.env"
git -C "$ROOT" rev-parse --verify --quiet "$TAG^{commit}" >/dev/null || { echo "no such tag: $TAG" >&2; exit 1; }

# The release's own tree, with its own version file - what a device of that release really runs.
WT="$(mktemp -d)/coffeepy-$TAG"
git -C "$ROOT" worktree add --detach --quiet "$WT" "$TAG"
trap 'git -C "$ROOT" worktree remove --force "$WT"' EXIT
COFFEEPY_VERSION="${TAG#v}" "$WT/scripts/write_version.sh" >/dev/null
rsync -a --delete --filter=". $WT/install/src-filter.txt" --exclude=/.device/ --exclude=/.claude/ \
	"$WT/" "$PI_USER@$PI_HOST:CoffeePy/"

# A downgrade can't go through the update mechanism: migrations only run forward, so the old
# code would meet a newer schema. Hence a clean slate - and units a later release added are
# removed, or the "old" device would already carry the new watcher's helpers.
"$PI" 'set -e
cd /opt/coffeepy 2>/dev/null && sudo docker compose down -v --remove-orphans || true
sudo rm -rf /opt/coffeepy/data/updates /opt/coffeepy/data/maintenance/status.json
for unit in /etc/systemd/system/coffeepy*.service /etc/systemd/system/coffeepy*.timer; do
	[ -e "$unit" ] || continue
	name=$(basename "$unit")
	[ -e ~/CoffeePy/install/"$name" ] || [ -e ~/CoffeePy/install/kiosk/"$name" ] && continue
	echo "removing $name (newer than this release)"
	sudo systemctl disable --now "$name" 2>/dev/null || true
	sudo rm -f "$unit"
done
sudo systemctl daemon-reload
cd ~/CoffeePy && sudo nohup ./install/install.sh --yes --force --skip-ap > ~/install.out 2>&1 < /dev/null &
echo "installing '"$TAG"' - follow ~/install.out"'
