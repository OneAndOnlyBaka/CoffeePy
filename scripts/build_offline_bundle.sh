#!/bin/bash
# Builds one tarball that install.sh can install a device from with no internet at all.
#
#   ./scripts/build_offline_bundle.sh [platform] [output.tar]
#   platform: linux/arm64 (default, 64-bit Raspberry Pi OS) or linux/arm/v7 (32-bit)
#
# On the device:
#   tar xf coffeepy-offline-<date>.tar -C /tmp
#   sudo /tmp/coffeepy-offline/repo/install/install.sh --offline=/tmp/coffeepy-offline
#
# What goes in, and why each part cannot simply be downloaded on the Pi later:
#
#   debs/          every package install/packages.txt asks for, plus their dependencies,
#                  resolved against the *real* Raspberry Pi OS archives - Raspbian and
#                  archive.raspberrypi.com, not Debian's. Complete with a Packages index,
#                  so apt on the device does the dependency ordering rather than dpkg
#                  being fed a directory in the hope that the order works out.
#   docker-compose Docker's own plugin build; Raspberry Pi OS has no package for it.
#   images.tar     coffeepy-web and nfc-bridge built for the target platform, plus the
#                  postgres/mosquitto images - built here because building them on a Pi 3
#                  means compiling psutil and pyscard on a 1.2GHz board.
#   repo/          this checkout, filtered the same way install.sh filters it.
#
# Requirements here: docker (or podman aliased to it) and, on an x86 machine, working
# binfmt emulation for the target architecture - the same qemu-user-static setup a
# cross-build needs. Without it the ARM containers below cannot run at all.
#
# The bundle carries the complete recursive dependency closure, not just what a solver
# thinks is missing - so it does not matter what the collection container happens to have
# installed. It does mean the bundle is larger than the download an online install would
# make, and that it is tied to one OS release: build it for the release the device runs
# (RELEASE=trixie by default).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_DIR="${PROJECT_DIR}/install"

# The same default as build_update_bundle.sh: a bundle for the wrong architecture leaves a
# device that can't start a single container.
PLATFORM="${1:-linux/arm64}"
OUTPUT="${2:-${PROJECT_DIR}/coffeepy-offline-$(date +%Y%m%d).tar}"
RELEASE="${RELEASE:-trixie}"
DOCKER="${DOCKER:-docker}"
SKIP_IMAGES="${SKIP_IMAGES:-0}"
# DEBS_ONLY=1: just the packages and the Compose plugin, as one tar at OUTPUT - what an
# update bundle carries for an offline device's security updates (build_update_bundle.sh).
DEBS_ONLY="${DEBS_ONLY:-0}"
# Another list than the installer's - an OS bundle's (build_os_bundle.sh), which may be a
# device's whole dpkg list. SKIP_MISSING=1 leaves out what the archives don't have (a package
# installed by hand, or from elsewhere) instead of failing.
PACKAGE_LIST="${PACKAGE_LIST:-${INSTALL_DIR}/packages.txt}"
SKIP_MISSING="${SKIP_MISSING:-0}"

# shellcheck source=../install/versions.env
. "${INSTALL_DIR}/versions.env"

case "${PLATFORM}" in
	linux/arm/v7) DEB_ARCH="armhf"; COMPOSE_ASSET="armv7";   COMPOSE_SHA="${COMPOSE_SHA256_armhf}" ;;
	linux/arm64)  DEB_ARCH="arm64"; COMPOSE_ASSET="aarch64"; COMPOSE_SHA="${COMPOSE_SHA256_arm64}" ;;
	*) echo "Unsupported platform ${PLATFORM} (linux/arm/v7 or linux/arm64)" >&2; exit 1 ;;
esac

log() { printf '\033[1;34m==\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

command -v "${DOCKER}" >/dev/null 2>&1 || die "${DOCKER} is required."
command -v rsync >/dev/null 2>&1 || die "rsync is required."

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
BUNDLE="${STAGE}/coffeepy-offline"
mkdir -p "${BUNDLE}/debs"

log "Target ${PLATFORM} (${DEB_ARCH}), Raspberry Pi OS ${RELEASE}"

# --------------------------------------------------------------------------- packages

log "Collecting packages from the Raspberry Pi OS archives"
# Debian's own image plus the two Raspberry Pi archives: Raspbian carries the armhf rebuild
# of Debian, archive.raspberrypi.com the Pi-specific packages (chromium, rpd-plym-splash).
# Both are what a real Pi has configured, and only there do these names resolve.
"${DOCKER}" run --rm --platform "${PLATFORM}" \
	-v "$(realpath "${PACKAGE_LIST}")":/packages.txt:ro \
	-v "${BUNDLE}/debs":/out \
	-e SKIP_MISSING="${SKIP_MISSING}" \
	"debian:${RELEASE}-slim" bash -euc '
		export DEBIAN_FRONTEND=noninteractive
		apt-get update -qq
		apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg dpkg-dev >/dev/null

		# Debian trixie verifies archive signatures with sqv (Sequoia), whose default policy
		# rejects the Raspberry Pi archive keys: their binding signatures are SHA-1, which it
		# has refused since 2026-02. Both repositories then come out as "not signed" and no
		# package can be downloaded at all. Relaxing exactly that one hash rule keeps the
		# signatures being *checked* - against the same keys a Pi trusts - instead of
		# reaching for [trusted=yes] and checking nothing.
		cat > /etc/coffeepy-sequoia.toml <<-POLICY
			[hash_algorithms.sha1]
			collision_resistance = "always"
			second_preimage_resistance = "always"
		POLICY
		export SEQUOIA_CRYPTO_POLICY=/etc/coffeepy-sequoia.toml

		arch="$(dpkg --print-architecture)"
		if [ "${arch}" = "armhf" ]; then
			curl -fsSL http://raspbian.raspberrypi.com/raspbian.public.key \
				| gpg --dearmor -o /usr/share/keyrings/raspbian.gpg
			echo "deb [signed-by=/usr/share/keyrings/raspbian.gpg] http://raspbian.raspberrypi.com/raspbian/ '"${RELEASE}"' main contrib non-free rpi" \
				> /etc/apt/sources.list.d/raspbian.list
			: > /etc/apt/sources.list
			rm -f /etc/apt/sources.list.d/debian.sources
		fi
		curl -fsSL http://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
			| gpg --dearmor -o /usr/share/keyrings/raspberrypi.gpg
		echo "deb [signed-by=/usr/share/keyrings/raspberrypi.gpg] http://archive.raspberrypi.com/debian/ '"${RELEASE}"' main" \
			> /etc/apt/sources.list.d/raspi.list

		apt-get update -qq
		packages="$(grep -vE "^\s*#|^\s*$" /packages.txt | tr "\n" " ")"

		# Fail loudly here rather than on the device: a name that does not resolve in these
		# archives would otherwise turn into a bundle that is quietly incomplete.
		missing=""
		for p in ${packages}; do
			candidate="$(apt-cache policy "${p}" 2>/dev/null | awk "/Candidate:/ {print \$2; exit}")"
			[ -n "${candidate}" ] && [ "${candidate}" != "(none)" ] || missing="${missing} ${p}"
		done
		if [ -n "${missing}" ] && [ "${SKIP_MISSING}" = 1 ]; then
			echo "Left out, not in the archives for ${arch}:${missing}" >&2
			for p in ${missing}; do packages=" ${packages} "; packages="${packages/ ${p} / }"; done
		elif [ -n "${missing}" ]; then
			echo "Not available for ${arch}:${missing}" >&2; exit 1
		fi

		# Two things this must not do, both learned the hard way:
		#
		#   * not "apt-get install --download-only": that solves against what is installed
		#     in this container - a Debian base pointed at Raspbian archives - and apt
		#     rightly refuses to work with that mongrel ("held broken packages").
		#   * not resolve against the dpkg status of this container either: for a package whose
		#     installed version does not exist in Raspbian, apt reports that very version
		#     as the candidate (its source being the status file) and then cannot download
		#     it. Emptying the status makes the resolution come purely from the archives -
		#     libudev1 then comes out as 257.9-1~deb13u1+rpi1 rather than the Debian
		#     build 257.13-1~deb13u1.
		#
		# Walking the dependency graph instead of solving also makes the closure complete
		# by construction, whatever the device happens to have installed already.
		empty_status="-o Dir::State::status=/dev/null"

		deps="$(apt-cache ${empty_status} depends --recurse --no-recommends --no-suggests \
			--no-conflicts --no-breaks --no-replaces --no-enhances ${packages} \
			| grep "^[a-zA-Z0-9]" | sort -u)"
		echo "downloading $(echo "${deps}" | wc -w) packages (the full dependency closure)"

		# One pass for all of them: under qemu every apt start costs seconds. Virtual
		# packages never reach here - apt-cache prints those in angle brackets, which the
		# grep drops.
		# Sandbox::User=root: apt drops to the _apt user to download, which cannot read
		# this directory - it copes, but warns once per package. Nothing here is worth
		# 700 lines of that.
		apt-get ${empty_status} -o APT::Sandbox::User=root download ${deps}

		mv ./*.deb /out/
		cd /out && dpkg-scanpackages -m . > Packages && gzip -9kf Packages
		echo "collected $(ls -1 /out/*.deb | wc -l) packages, $(du -sh /out | cut -f1)"
	'

# --------------------------------------------------------------------------- compose

log "Fetching the Docker Compose ${COMPOSE_VERSION} plugin (${COMPOSE_ASSET})"
curl -fsSL -o "${BUNDLE}/docker-compose" \
	"https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${COMPOSE_ASSET}"
echo "${COMPOSE_SHA}  ${BUNDLE}/docker-compose" | sha256sum -c - >/dev/null \
	|| die "Compose download failed its checksum."

if [ "${DEBS_ONLY}" = "1" ]; then
	tar -cf "${OUTPUT}" -C "${BUNDLE}" debs docker-compose
	log "Done: ${OUTPUT} ($(du -h "${OUTPUT}" | cut -f1))"
	exit 0
fi

# --------------------------------------------------------------------------- images

if [ "${SKIP_IMAGES}" = "1" ]; then
	log "SKIP_IMAGES=1 - leaving images.tar out (the bundle will be incomplete)"
else
	log "Building the app images for ${PLATFORM} - the slow part, they cross-build under qemu"
	IMAGES_ONLY=1 DOCKER="${DOCKER}" "${SCRIPT_DIR}/build_update_bundle.sh" "${PLATFORM}" "${BUNDLE}/images.tar"
fi

# --------------------------------------------------------------------------- source

log "Adding the source tree"
mkdir -p "${BUNDLE}/repo"
rsync -a --filter=". ${INSTALL_DIR}/src-filter.txt" "${PROJECT_DIR}/" "${BUNDLE}/repo/"
[ -e "${BUNDLE}/repo/.env" ] && die "REFUSING: a .env leaked into the bundle - check install/src-filter.txt"

{
	echo "built: $(date -Is)"
	echo "platform: ${PLATFORM}"
	echo "release: ${RELEASE}"
	echo "compose: ${COMPOSE_VERSION}"
	echo "packages: $(ls -1 "${BUNDLE}/debs"/*.deb 2>/dev/null | wc -l)"
	echo "git: $(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout')"
} > "${BUNDLE}/manifest.txt"

log "Packing ${OUTPUT}"
tar -C "${STAGE}" -cf "${OUTPUT}" coffeepy-offline

echo
log "Done: ${OUTPUT} ($(du -h "${OUTPUT}" | cut -f1))"
cat "${BUNDLE}/manifest.txt" | sed 's/^/   /'
echo
echo "   On the device (no internet needed):"
echo "     tar xf $(basename "${OUTPUT}") -C /tmp"
echo "     sudo /tmp/coffeepy-offline/repo/install/install.sh --offline=/tmp/coffeepy-offline"
