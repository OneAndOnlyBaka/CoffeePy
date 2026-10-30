#!/bin/bash
# Builds a signed update bundle of a release (or of this working tree) on the test Pi and
# leaves it there as ~/bundles/NAME.tar - the device builds natively, this machine has no
# arm64 Docker.
#
#   bundle.sh v2.0.1 [NAME]     a release tag
#   bundle.sh . [NAME]          this working tree, uncommitted changes included
#   bundle.sh DIR [NAME]        a copy of it, e.g. with a migration only the test needs
#                               (run scripts/write_version.sh before copying)
#
# Takes 10-20 min (image build + docker save): run it in the background.
set -euo pipefail

SRC="${1:?usage: bundle.sh TAG|. [NAME]}"
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PI="$ROOT/.claude/skills/pi-device/scripts/pi.sh"
. "$ROOT/.device/pi.env"
KEY="${KEY:-$HOME/.config/coffeepy/update-signing.key}"
[ -f "$KEY" ] || { echo "no signing key at $KEY" >&2; exit 1; }

if [ "$SRC" = "." ]; then
	TREE="$ROOT"; NAME="${2:-current}"
	"$ROOT/scripts/write_version.sh" >/dev/null
elif [ -d "$SRC" ]; then
	TREE="$(cd "$SRC" && pwd)"; NAME="${2:-$(basename "$TREE")}"
else
	TREE="$(mktemp -d)/coffeepy-$SRC"; NAME="${2:-$SRC}"
	git -C "$ROOT" worktree add --detach --quiet "$TREE" "$SRC"
	trap 'git -C "$ROOT" worktree remove --force "$TREE"' EXIT
	COFFEEPY_VERSION="${SRC#v}" "$TREE/scripts/write_version.sh" >/dev/null
fi
# A directory called coffeepy: a release without `name:` in its compose file names the
# nfc-bridge image after the directory it is built in - the device expects coffeepy-nfc-bridge.
"$PI" 'mkdir -p ~/coffeepy-src/coffeepy'
rsync -a --delete --filter=". $TREE/install/src-filter.txt" --exclude=/.device/ --exclude=/.claude/ \
	"$TREE/" "$PI_USER@$PI_HOST:coffeepy-src/coffeepy/"

# The build re-tags coffeepy-web/nfc-bridge:latest; the running ones get their tags back, or
# the update that follows finds nothing to change (and prune could take the running image).
"$PI" "set -e
mkdir -p ~/bundles
web=\$(sudo docker image inspect -f '{{.Id}}' coffeepy-web:latest 2>/dev/null || true)
nfc=\$(sudo docker image inspect -f '{{.Id}}' coffeepy-nfc-bridge:latest 2>/dev/null || true)
cd ~/coffeepy-src/coffeepy && sudo scripts/build_update_bundle.sh --unsigned --output ~/bundles/$NAME.tar > ~/bundles/$NAME.out 2>&1 \
	|| { tail -20 ~/bundles/$NAME.out; exit 1; }
[ -n \"\$web\" ] && sudo docker tag \"\$web\" coffeepy-web:latest
[ -n \"\$nfc\" ] && sudo docker tag \"\$nfc\" coffeepy-nfc-bridge:latest
tail -1 ~/bundles/$NAME.out"

# Never the private key on the Pi: only the manifest comes here to be signed.
work="$(mktemp -d)"; trap 'rm -rf "$work"; [ "$SRC" = "." ] || [ -d "$SRC" ] || git -C "$ROOT" worktree remove --force "$TREE"' EXIT
"$PI" "tar -xOf ~/bundles/$NAME.tar manifest.json" > "$work/manifest.json"
openssl pkeyutl -sign -rawin -inkey "$KEY" -in "$work/manifest.json" -out "$work/manifest.sig"
"$PI" "set -e; d=\$(mktemp -d -p ~); cat > \$d/manifest.sig; cd \$d
sudo tar --delete -f ~/bundles/$NAME.tar manifest.sig 2>/dev/null || true
sudo tar -rf ~/bundles/$NAME.tar manifest.sig; cd; rm -rf \$d
echo \"signed: ~/bundles/$NAME.tar (\$(du -h ~/bundles/$NAME.tar | cut -f1))\"" < "$work/manifest.sig"
