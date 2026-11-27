#!/bin/bash
# Copies this working tree to ~/CoffeePy on the Pi - the checkout install.sh runs from.
# Uses the installer's own filter, so what lands there is what a real install would copy
# on to /opt/coffeepy (no .env, no .venv, no .git).
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
. "$ROOT/.device/pi.env"
"$ROOT/scripts/write_version.sh" >/dev/null
# .render/: the product video's working copy (several GB, git-ignored) - not something a Pi needs.
rsync -a --delete --filter=". $ROOT/install/src-filter.txt" --exclude=/.device/ --exclude=/.claude/ --exclude=/.render/ \
	"$ROOT/" "$PI_USER@$PI_HOST:CoffeePy/"
echo "synced to $PI_HOST:~/CoffeePy"
