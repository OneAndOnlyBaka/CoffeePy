#!/bin/bash
# Runs a command on the test Pi (or opens a shell with no arguments).
#
#   pi.sh 'uptime'
#   pi.sh --setup        install this machine's SSH key + passwordless sudo, once per flash
#
# Connection data lives in <repo>/.device/pi.env (PI_HOST, PI_USER, PI_PASSWORD), which is
# excluded via .git/info/exclude and never committed.
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
ENV_FILE="$ROOT/.device/pi.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE - see the pi-device skill's Setup section." >&2; exit 1; }
# shellcheck source=/dev/null
. "$ENV_FILE"
TARGET="$PI_USER@$PI_HOST"

if [ "${1:-}" = "--setup" ]; then
	# A freshly flashed Pi only knows the password. SSH_ASKPASS feeds it to ssh-copy-id
	# without a terminal; setsid detaches the tty so ssh actually asks the helper.
	askpass="$(mktemp)"; trap 'rm -f "$askpass"' EXIT
	printf '#!/bin/sh\n. "%s"\nprintf "%%s\\n" "$PI_PASSWORD"\n' "$ENV_FILE" > "$askpass"; chmod 700 "$askpass"
	ssh-keygen -R "$PI_HOST" >/dev/null 2>&1 || true
	SSH_ASKPASS="$askpass" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 setsid -w \
		ssh-copy-id -o StrictHostKeyChecking=accept-new "$TARGET" </dev/null
	ssh -o BatchMode=yes "$TARGET" "printf '%s\n' '$PI_PASSWORD' | sudo -S sh -c \
		'echo \"$PI_USER ALL=(ALL) NOPASSWD: ALL\" > /etc/sudoers.d/010-$PI_USER-nopasswd && chmod 440 /etc/sudoers.d/010-$PI_USER-nopasswd'"
	ssh -o BatchMode=yes "$TARGET" 'sudo -n true && echo "ready: key login + passwordless sudo"'
	exit 0
fi

exec ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 "$TARGET" "$@"
