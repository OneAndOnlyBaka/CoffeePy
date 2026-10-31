#!/bin/bash
# Run on a DEV MACHINE with internet access: builds an OS bundle - updates for the operating
# system packages of an offline device - which an admin uploads via /dashboard/update/ like an
# app update. The update watcher (install/update_watcher.py) applies it with the script that
# comes inside it (install/os_update.sh), which only updates what is installed already.
#
#   scripts/build_os_bundle.sh [options]
#     --platform P     linux/arm64 (default - 64-bit Raspberry Pi OS) or linux/arm/v7
#     --packages FILE  the packages to bring up to date, one per line - best the device's own
#                      list (dashboard → Update → "Installed packages"); default install/packages.txt
#     --release R      the Raspberry Pi OS release the device runs (default trixie); a device
#                      on another release refuses the bundle
#     --output FILE    default coffeepy-os-<release>-<date>-<arch>-unsigned.tar
#
# The bundle comes out unsigned; sign it like the release's app bundles:
#   scripts/sign_update_bundle.sh FILE --key ~/.config/coffeepy/update-signing.key
#
# Which packages go in - and whether they suit the device - is up to whoever builds it.
# CoffeePy only ships the means: it runs on more than one kind of hardware, and only the
# operator knows what that device's system should carry. Packages are resolved against the
# Raspberry Pi OS archives (scripts/build_offline_bundle.sh); one the archives don't have is
# left out with a warning. Needs docker and, on x86, binfmt emulation for the target.
set -euo pipefail
cd "$(dirname "$0")/.."

PLATFORM="linux/arm64"
PACKAGES="install/packages.txt"
RELEASE="trixie"
OUT=""
DOCKER="${DOCKER:-docker}"

while [ $# -gt 0 ]; do
	case "$1" in
		--platform) PLATFORM="$2"; shift ;;
		--packages) PACKAGES="$2"; shift ;;
		--release) RELEASE="$2"; shift ;;
		--output) OUT="$2"; shift ;;
		-h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		*) echo "Unknown option $1 (see --help)" >&2; exit 2 ;;
	esac
	shift
done

case "$PLATFORM" in
	linux/arm64) ARCH=arm64 ;;
	linux/arm/v7) ARCH=armhf ;;
	*) echo "Unsupported platform $PLATFORM (linux/arm64 or linux/arm/v7)" >&2; exit 2 ;;
esac
[ -f "$PACKAGES" ] || { echo "No package list at $PACKAGES" >&2; exit 2; }

BUILT_AT="$(date +%s)"
VERSION="os-${RELEASE}-$(date +%Y%m%d-%H%M)"
OUT="${OUT:-coffeepy-${VERSION}-${ARCH}-unsigned.tar}"

# Next to the output, not in /tmp: a whole system's packages don't fit a RAM-backed tmpfs.
workdir="$(mktemp -d "$(dirname "$OUT")/.coffeepy-os.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

echo "== $VERSION: collecting the packages of $PACKAGES for $PLATFORM =="
DEBS_ONLY=1 SKIP_MISSING=1 PACKAGE_LIST="$PACKAGES" RELEASE="$RELEASE" DOCKER="$DOCKER" \
	scripts/build_offline_bundle.sh "$PLATFORM" "$workdir/os.tar"
tar --delete -f "$workdir/os.tar" docker-compose  # the offline install's Compose plugin: not a package
cp install/os_update.sh "$workdir/os-update.sh"

python3 - "$workdir" "$VERSION" "$BUILT_AT" "$PLATFORM" "$RELEASE" <<'PY'
import hashlib, json, sys
from pathlib import Path

work, version, built_at, platform, release = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
files = {}
for name in ("os.tar", "os-update.sh"):
    digest = hashlib.sha256()
    with open(work / name, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    files[name] = digest.hexdigest()
size_mb = sum((work / n).stat().st_size for n in files) // (1 << 20)
manifest = {
    "format": 2,
    "kind": "os",
    "version": version,
    "built_at": built_at,
    "platform": platform,
    "release": release,
    # Unpacked, and then unpacked again by dpkg.
    "required_mb": 3 * size_mb + 200,
    "files": files,
}
(work / "manifest.json").write_text(json.dumps(manifest, indent=1))
PY

tar -cf "$OUT" -C "$workdir" manifest.json os.tar os-update.sh
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1)), $VERSION, UNSIGNED - sign it with:"
echo "  scripts/sign_update_bundle.sh $OUT --key ~/.config/coffeepy/update-signing.key"
