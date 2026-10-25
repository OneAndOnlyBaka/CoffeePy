#!/bin/bash
# Copies this working tree to ~/CoffeePy on the Pi - the checkout install.sh runs from.
# Uses the installer's own filter, so what lands there is what a real install would copy
# on to /opt/coffeepy (no .env, no .venv, no .git).
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
. "$ROOT/.device/pi.env"
rsync -a --delete --filter=". $ROOT/install/src-filter.txt" --exclude=/.device/ --exclude=/.claude/ \
	"$ROOT/" "$PI_USER@$PI_HOST:CoffeePy/"
echo "synced to $PI_HOST:~/CoffeePy"
