#!/bin/bash
# Run on a DEV MACHINE with internet access (not on the offline Pi). Cross-builds every
# image docker-compose.yml defines (coffeepy-web, shared by migrate/web/mqtt-
# listener/backup, and nfc-bridge from its own Dockerfile under nfc-cardreader/) for the
# Pi's architecture, pulls the two upstream images (db, mosquitto), and bundles them
# together with docker-compose.yml itself into a single tar an admin can carry over (USB
# stick / laptop joined to the Pi's WiFi AP) and upload via /dashboard/update/.
#
# docker-compose.yml is the only source of truth for both what's in the bundle and what
# ends up running on the Pi (see update_watcher.sh) - nothing here is a hand-maintained
# list, and the two can never quietly drift apart. Shipping the compose file itself is
# also how a new container (a new service block) gets onto the Pi at all.
set -euo pipefail

PLATFORM="${1:-linux/arm64}"       # linux/arm64 (Pi 3/4/5, 64-bit) or linux/arm/v7 (32-bit)
OUT="${2:-coffeepy-update-$(date +%Y%m%d%H%M).tar}"

cd "$(dirname "$0")/.."

export DOCKER_DEFAULT_PLATFORM="$PLATFORM"

# Next to the output rather than in /tmp: the image tarball is several hundred MB, and on
# Debian trixie or Fedora /tmp is a RAM-backed tmpfs that it does not fit into.
workdir="$(mktemp -d "$(dirname "$OUT")/.coffeepy-bundle.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

# docker-compose.yml's env_file makes every compose command fail without a .env - and a
# clean checkout has none. Nothing from it ends up in the bundle, so the example will do.
if [ ! -f .env ]; then
    cp .env.example .env
    trap 'rm -rf "$workdir"; rm -f .env' EXIT
fi

echo "== Building coffeepy-web + nfc-bridge for $PLATFORM (docker-compose.yml is the source of truth) =="
docker compose build

echo "== Pulling db + mosquitto for $PLATFORM =="
docker compose pull db mosquitto

# Every image any service resolves to, built or pulled, deduplicated (web/migrate/
# mqtt-listener/backup all share the same coffeepy-web:latest tag).
# Not a process substitution: its exit status is lost, and a failing `compose config`
# would only surface later as a confusing "docker save requires at least 1 argument".
images="$(docker compose config --images)"
mapfile -t IMAGES < <(sort -u <<<"$images")
[ "${#IMAGES[@]}" -gt 0 ] && [ -n "${IMAGES[0]}" ] || { echo "No images in docker-compose.yml?" >&2; exit 1; }
echo "== Bundling images: ${IMAGES[*]} =="

docker save -o "$workdir/images.tar" "${IMAGES[@]}"
cp docker-compose.yml "$workdir/docker-compose.yml"

tar -cf "$OUT" -C "$workdir" images.tar docker-compose.yml

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1)) - upload this via /dashboard/update/"
