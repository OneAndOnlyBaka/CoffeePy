#!/bin/bash
# The script of an OS bundle (scripts/build_os_bundle.sh). The update watcher runs it as root,
# with the bundle's unpacked os/ as its argument. It travels inside the bundle, listed in the
# signed manifest like the packages - so a device only ever runs a package installation that
# was signed together with its packages, and there is no other way onto it.
#
#   os_update.sh DIR      DIR/debs: the packages and their Packages index
#
# It only brings what is installed up to the bundle's versions, plus what those newly depend
# on (a new kernel's versioned package, say): a bundle is no way to install anything new.
# Re-running it is harmless, which is what an upload after an interrupted update relies on.
set -Eeuo pipefail

DIR="${1:?usage: os_update.sh DIR}"
[ -f "${DIR}/debs/Packages" ] || { echo "No package index in ${DIR}/debs - not an OS bundle" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

# What a power cut left half configured, before anything new is unpacked over it.
dpkg --configure -a

# A file:// archive, so apt does the dependency ordering. [trusted=yes] because the index has
# no Release signature of its own - the manifest's signature covers every byte of it.
# sourcelist/sourceparts are overridden so only this source counts, and the device's
# configured (and unreachable) archives are neither asked nor forgotten (List-Cleanup=0).
list="$(mktemp)"
trap 'rm -f "${list}"' EXIT
echo "deb [trusted=yes] file://${DIR}/debs ./" > "${list}"
apt_local() {
	apt-get -o Dir::Etc::sourcelist="${list}" -o Dir::Etc::sourceparts=/dev/null \
		-o APT::Get::List-Cleanup=0 "$@"
}
installed() { dpkg-query -W -f '${Package} ${Version}\n' | sort; }
before="$(installed)"
apt_local update -q
# The device's own configuration files stay: an update must not undo what the installer set.
apt_local -y -q --with-new-pkgs --no-install-recommends \
	-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
apt-get clean

changed="$(comm -13 <(printf '%s\n' "${before}") <(installed) | cut -d' ' -f1)"
echo "Brought up to date: $(printf '%s\n' "${changed}" | grep -c . || true) package(s)."
# Only Ubuntu's update-notifier reliably asks for a restart, so it is worked out here: what only
# takes effect at boot (kernel, firmware) or runs in every process (libc, systemd). The update
# watcher restarts the device when this file is there.
restart="$(printf '%s\n' "${changed}" \
	| grep -E '^(linux-image|raspi-firmware|raspberrypi-(kernel|bootloader)|firmware-|libc6$|systemd$)' || true)"
if [ -n "${restart}" ]; then
	printf '%s\n' "${restart}" >> /run/reboot-required.pkgs
	touch /run/reboot-required
fi
# What apt kept back: something installed needs exactly the old version and isn't in the bundle
# (libexpat1-dev holds libexpat1, measured on the test Pi). Silently, a security fix would stay
# out - so it is said, with the remedy.
behind="$(awk '/^Package:/ {p = $2} /^Version:/ {print p, $2}' "${DIR}/debs/Packages" | while read -r p v; do
	have="$(dpkg-query -W -f '${Version}' "${p}" 2>/dev/null)" || continue
	if [ -n "${have}" ] && dpkg --compare-versions "${have}" lt "${v}"; then echo "${p}"; fi
done | sort -u | tr '\n' ' ')"
[ -z "${behind}" ] || echo "Kept back, as something installed needs the old version - add what depends on them to the list: ${behind}"
[ -e /run/reboot-required ] && echo "A restart is needed for: $(sort -u /run/reboot-required.pkgs 2>/dev/null | tr '\n' ' ')"
exit 0
