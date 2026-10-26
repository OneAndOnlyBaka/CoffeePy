#!/bin/bash
# Run on a DEV MACHINE with internet access (not on the offline Pi): builds a signed update
# bundle an admin uploads via /dashboard/update/ (install/update_watcher.py applies it).
# scripts/update_builder.sh asks for all of this interactively.
#
#   scripts/build_update_bundle.sh [options]
#     --platform P          linux/arm64 (default - 64-bit Raspberry Pi OS) or linux/arm/v7
#     --output FILE         default coffeepy-update-<version>.tar
#     --no-install          leave install/ (watcher, storage helper, units, kiosk session) out
#     --with-os-packages    add the Raspberry Pi OS packages of install/packages.txt, for an
#                           offline device's security updates (slow: resolves them under qemu)
#     --key FILE            signing key (default ~/.config/coffeepy/update-signing.key,
#                           create one with scripts/update_signing_key.sh)
#     --unsigned            no signature - devices refuse it unless UPDATE_ALLOW_UNSIGNED=true
#
# docker-compose.yml is the only source of truth for what's in the bundle and what ends up
# running on the Pi - nothing here is a hand-maintained list of images. Shipping the compose
# file itself is also how a new container (a new service block) gets onto the Pi at all.
#
# IMAGES_ONLY=1 scripts/build_update_bundle.sh PLATFORM FILE writes just the `docker save`
# of the images - what scripts/build_offline_bundle.sh puts into an offline install.
set -euo pipefail
cd "$(dirname "$0")/.."

PLATFORM="linux/arm64"
OUT=""
WITH_INSTALL=1
WITH_OS=0
KEY="${COFFEEPY_SIGNING_KEY:-$HOME/.config/coffeepy/update-signing.key}"
SIGN=1
DOCKER="${DOCKER:-docker}"

if [ "${IMAGES_ONLY:-0}" = "1" ]; then
	PLATFORM="${1:-$PLATFORM}"
	OUT="${2:?output file}"
else
	while [ $# -gt 0 ]; do
		case "$1" in
			--platform) PLATFORM="$2"; shift ;;
			--output) OUT="$2"; shift ;;
			--no-install) WITH_INSTALL=0 ;;
			--with-os-packages) WITH_OS=1 ;;
			--key) KEY="$2"; shift ;;
			--unsigned) SIGN=0 ;;
			*) echo "Unknown option $1 (see the header of this script)" >&2; exit 2 ;;
		esac
		shift
	done
fi

case "$PLATFORM" in
	linux/arm64|linux/arm/v7) ;;
	*) echo "Unsupported platform $PLATFORM (linux/arm64 or linux/arm/v7)" >&2; exit 2 ;;
esac
if [ "$SIGN" = "1" ] && [ "${IMAGES_ONLY:-0}" != "1" ] && [ ! -f "$KEY" ]; then
	echo "No signing key at $KEY - create one with scripts/update_signing_key.sh, or pass --unsigned." >&2
	exit 2
fi

VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo "unversioned")"
BUILT_AT="$(date +%s)"
OUT="${OUT:-coffeepy-update-${VERSION}.tar}"
export DOCKER_DEFAULT_PLATFORM="$PLATFORM" COFFEEPY_VERSION="$VERSION"

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

echo "== $VERSION: building coffeepy-web + nfc-bridge for $PLATFORM =="
"$DOCKER" compose build
echo "== Pulling every image the compose file does not build, for $PLATFORM =="
"$DOCKER" compose pull --ignore-buildable

# Every image any service resolves to, built or pulled, deduplicated (web/migrate/
# mqtt-listener/backup all share the same coffeepy-web:latest tag).
# Not a process substitution: its exit status is lost, and a failing `compose config`
# would only surface later as a confusing "docker save requires at least 1 argument".
images="$("$DOCKER" compose config --images)"
mapfile -t IMAGES < <(sort -u <<<"$images")
[ "${#IMAGES[@]}" -gt 0 ] && [ -n "${IMAGES[0]}" ] || { echo "No images in docker-compose.yml?" >&2; exit 1; }
echo "== Saving images: ${IMAGES[*]} =="
"$DOCKER" save -o "$workdir/images.tar" "${IMAGES[@]}"

if [ "${IMAGES_ONLY:-0}" = "1" ]; then
	mv "$workdir/images.tar" "$OUT"
	echo "Wrote $OUT"
	exit 0
fi

cp docker-compose.yml "$workdir/docker-compose.yml"
if [ "$WITH_INSTALL" = "1" ]; then
	echo "== Adding install/ =="
	tar -cf "$workdir/install.tar" --exclude=__pycache__ install
fi
if [ "$WITH_OS" = "1" ]; then
	echo "== Collecting Raspberry Pi OS packages (install/packages.txt) =="
	DEBS_ONLY=1 DOCKER="$DOCKER" scripts/build_offline_bundle.sh "$PLATFORM" "$workdir/os.tar"
fi

# The manifest: what is in the bundle (with checksums the watcher compares), and what the
# device needs to know before it touches anything.
python3 - "$workdir" "$VERSION" "$BUILT_AT" "$PLATFORM" <<'PY'
import hashlib, json, sys
from pathlib import Path

work, version, built_at, platform = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
files = {}
for name in ("images.tar", "docker-compose.yml", "install.tar", "os.tar"):
    path = work / name
    if path.is_file():
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        files[name] = digest.hexdigest()
# .env settings a new version may need: the example's defaults - never its placeholders
# for secrets, which every device has its own of already.
defaults = {}
for line in Path(".env.example").read_text().splitlines():
    key, sep, value = line.partition("=")
    if sep and not key.startswith("#") and value and value != "change-me":
        defaults[key.strip()] = value.strip()
size_mb = sum((work / n).stat().st_size for n in files) // (1 << 20)
manifest = {
    "format": 2,
    "version": version,
    "built_at": built_at,
    "platform": platform,
    # Unpacked and loaded, the images need about twice their size again for a moment.
    "required_mb": 2 * size_mb + 200,
    "env_defaults": defaults,
    "files": files,
}
(work / "manifest.json").write_text(json.dumps(manifest, indent=1))
PY

members=(manifest.json)
if [ "$SIGN" = "1" ]; then
	openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$workdir/manifest.json" -out "$workdir/manifest.sig"
	members+=(manifest.sig)
fi
for name in images.tar docker-compose.yml install.tar os.tar; do
	[ -f "$workdir/$name" ] && members+=("$name")
done
tar -cf "$OUT" -C "$workdir" "${members[@]}"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1)), version $VERSION$([ "$SIGN" = 1 ] || echo ", UNSIGNED") - upload it via /dashboard/update/"
