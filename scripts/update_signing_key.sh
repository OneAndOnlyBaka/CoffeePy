#!/bin/bash
# Creates the key pair update bundles are signed with (scripts/build_update_bundle.sh) - once,
# on the machine that builds them. The private key stays there; the public key goes into
# install/update-signing.pub, which the installer puts on every device
# (/etc/coffeepy/update-signing.pub) and the update watcher checks bundles against.
#
#   scripts/update_signing_key.sh [private key file]
#
# A new key pair means: commit install/update-signing.pub, and re-run the installer (or apply
# one bundle signed with the *old* key that carries the new public key) on every device.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY="${1:-${COFFEEPY_SIGNING_KEY:-$HOME/.config/coffeepy/update-signing.key}}"
if [ -e "$KEY" ]; then
	echo "Key exists: $KEY - not overwritten." >&2
else
	mkdir -p "$(dirname "$KEY")"
	(umask 077 && openssl genpkey -algorithm ed25519 -out "$KEY")
	echo "Created $KEY - keep it safe, it is what devices trust."
fi
openssl pkey -in "$KEY" -pubout -out install/update-signing.pub
echo "Wrote install/update-signing.pub"
