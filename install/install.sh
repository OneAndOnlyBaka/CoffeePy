#!/bin/bash
# CoffeePy installer for Raspberry Pi OS Lite.
#
# Turns a freshly flashed Raspberry Pi OS Lite into the coffee machine kiosk: the app
# stack in Docker, a Chromium kiosk on the touchscreen, the DS3231 clock, the ACR122U
# reader, and finally the standalone WiFi access point.
#
#   git clone <this repo> && cd CoffeePy
#   sudo ./install/install.sh
#
# Options:
#   --dry-run     print every action instead of performing it (safe to run anywhere)
#   --yes         don't ask before the steps that change networking
#   --force       re-run steps that are already recorded as done
#   --skip-ap     leave networking alone (keep the Pi on your existing WiFi/ethernet)
#   --only=STEP   run a single step (preflight packages app hardware kiosk services
#                 images ap verify)
#   --offline=DIR install from an unpacked offline bundle - no internet needed at all
#                 (see scripts/build_offline_bundle.sh)
#
# Design notes, because this replaced a script that had to be run twice and could not be
# re-run at all:
#
#   * Every step is idempotent and recorded in ${STATE_FILE}. Interrupted? Run it again.
#   * The access point comes last and is asked for, because switching to it drops the
#     network connection the earlier steps need - including the SSH session you may be
#     sitting in.
#   * Every file this touches outside /opt/coffeepy is backed up once, with a .coffeepy-bak
#     suffix, before the first modification.
#   * Package names are verified against the archives *before* installing anything: the
#     ones this needs differ between Debian and Raspberry Pi OS, and finding that out
#     half-way through leaves a half-configured device.
# -E so the ERR trap below also fires inside the step functions - without it a failure
# deep in a step just exits with a bare status and no hint about where it happened.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="/opt/coffeepy"
STATE_FILE="/var/lib/coffeepy/install-state"
LOG_FILE="/var/log/coffeepy-install.log"

# Pinned artefacts and the package list live in their own files, shared with
# scripts/build_offline_bundle.sh - an offline bundle that disagreed with the installer
# about either would be worse than no bundle at all.
# shellcheck source=versions.env
. "${SCRIPT_DIR}/versions.env"
PACKAGE_LIST="${SCRIPT_DIR}/packages.txt"

# The documented panel (Adafruit 2396, 1024x600) reports no usable EDID, and current
# Raspberry Pi OS drives the display through KMS, where the classic hdmi_group/hdmi_mode/
# hdmi_cvt lines are ignored - so the screen stays black unless the mode is forced on the
# kernel command line. Set VIDEO_MODE= (empty) for a display that negotiates properly.
VIDEO_MODE="${VIDEO_MODE-video=HDMI-A-1:1024x600M@60D}"

AP_ADDRESS="192.168.4.1"

DRY_RUN=0
ASSUME_YES=0
FORCE=0
SKIP_AP=0
ONLY=""
OFFLINE_DIR=""

ALL_STEPS="preflight packages app hardware kiosk services images ap verify"


# --------------------------------------------------------------------------- plumbing

log()  { printf '\033[1;34m==\033[0m %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

run() {
	if [ "${DRY_RUN}" = "1" ]; then
		printf '   [dry-run] %s\n' "$*"
		return 0
	fi
	"$@"
}

# Writes stdin to a file, with the same dry-run behaviour as run().
write_file() {
	local path="$1" mode="${2:-0644}" content
	content="$(cat)"
	if [ "${DRY_RUN}" = "1" ]; then
		printf '   [dry-run] write %s (%s, %d bytes)\n' "${path}" "${mode}" "${#content}"
		return 0
	fi
	install -d "$(dirname "${path}")"
	printf '%s\n' "${content}" > "${path}"
	chmod "${mode}" "${path}"
}

# One backup per file, ever - so re-running the installer cannot overwrite the pristine
# copy with an already-modified one.
backup_once() {
	local path="$1"
	[ -f "${path}" ] || return 0
	[ -f "${path}.coffeepy-bak" ] && return 0
	run cp -a "${path}" "${path}.coffeepy-bak"
}

# Appends a line unless it is already there. The grep is anchored on the whole line so a
# commented-out variant of the same setting does not count as present.
ensure_line() {
	local path="$1" line="$2"
	if [ -f "${path}" ] && grep -qxF "${line}" "${path}"; then
		return 0
	fi
	backup_once "${path}"
	if [ "${DRY_RUN}" = "1" ]; then
		printf '   [dry-run] append to %s: %s\n' "${path}" "${line}"
		return 0
	fi
	install -d "$(dirname "${path}")"
	printf '%s\n' "${line}" >> "${path}"
}

step_done()   { [ -f "${STATE_FILE}" ] && grep -qxF "$1" "${STATE_FILE}"; }
mark_done()   { [ "${DRY_RUN}" = "1" ] && return 0; install -d "$(dirname "${STATE_FILE}")"; grep -qxF "$1" "${STATE_FILE}" 2>/dev/null || printf '%s\n' "$1" >> "${STATE_FILE}"; }

confirm() {
	[ "${ASSUME_YES}" = "1" ] && return 0
	[ "${DRY_RUN}" = "1" ] && return 0
	local answer
	read -r -p "   $1 [y/N] " answer
	[ "${answer}" = "y" ] || [ "${answer}" = "Y" ]
}

# head closing the pipe early makes tr die of SIGPIPE, which under `set -o pipefail`
# counts as a failed pipeline - hence the tolerated status plus an explicit length check,
# so a genuinely empty read can never turn into a short (or blank) secret.
random_token() {
	local length="$1" token
	token="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c "${length}" || true)"
	[ "${#token}" -eq "${length}" ] || die "Could not read ${length} random characters from /dev/urandom."
	printf '%s' "${token}"
}

# --------------------------------------------------------------------------- steps

step_preflight() {
	local problems=0

	if [ "$(id -u)" -ne 0 ]; then
		die "Run as root: sudo $0"
	fi

	local model="unknown"
	[ -r /proc/device-tree/model ] && model="$(tr -d '\0' < /proc/device-tree/model)"
	info "Board:        ${model}"
	case "${model}" in
		*"Raspberry Pi"*) ;;
		*) warn "This does not look like a Raspberry Pi."; problems=$((problems + 1)) ;;
	esac

	local arch; arch="$(dpkg --print-architecture)"
	info "Architecture: ${arch}"
	case "${arch}" in
		armhf|arm64) ;;
		*) warn "Unsupported architecture ${arch} (armhf or arm64 expected)."; problems=$((problems + 1)) ;;
	esac

	local release="unknown"
	[ -r /etc/os-release ] && release="$(. /etc/os-release && echo "${VERSION_CODENAME:-$ID}")"
	info "Release:      ${release}"

	local ram_mb; ram_mb=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
	info "RAM:          ${ram_mb} MB"
	if [ "${ram_mb}" -lt 900 ]; then
		warn "Less than 1GB of RAM - Postgres, five containers and a browser will not fit."
		problems=$((problems + 1))
	elif [ "${ram_mb}" -lt 1200 ] && [ "${arch}" = "arm64" ]; then
		warn "1GB of RAM with a 64-bit OS is tight; the 32-bit image leaves more headroom."
	fi

	local free_mb; free_mb=$(( $(df --output=avail -k / | tail -1) / 1024 ))
	info "Free on /:    ${free_mb} MB"
	if [ "${free_mb}" -lt 6000 ]; then
		warn "Less than 6GB free - building the app images needs room."
		problems=$((problems + 1))
	fi

	if [ -n "${OFFLINE_DIR}" ]; then
		info "Source:       offline bundle at ${OFFLINE_DIR}"
		local missing_parts=()
		[ -d "${OFFLINE_DIR}/debs" ]                  || missing_parts+=("debs/")
		[ -f "${OFFLINE_DIR}/docker-compose" ]        || missing_parts+=("docker-compose")
		[ -f "${OFFLINE_DIR}/images.tar" ]            || missing_parts+=("images.tar")
		if [ "${#missing_parts[@]}" -gt 0 ]; then
			warn "Incomplete bundle - missing: ${missing_parts[*]}"
			problems=$((problems + 1))
		fi
		if [ -f "${OFFLINE_DIR}/manifest.txt" ]; then
			info "Bundle:       $(grep -E '^(built|platform|release):' "${OFFLINE_DIR}/manifest.txt" | tr '\n' ' ')"
		fi
	elif run_quiet getent hosts deb.debian.org; then
		info "Network:      name resolution works"
	else
		warn "Cannot resolve deb.debian.org - install with --offline=DIR, or connect this Pi"
		warn "to the internet until the last step."
		problems=$((problems + 1))
	fi

	if [ ! -f "${PROJECT_DIR}/docker-compose.yml" ] || [ ! -f "${PROJECT_DIR}/manage.py" ]; then
		die "Run this from a checkout of the CoffeePy repository (no docker-compose.yml next to install/)."
	fi

	if [ "${problems}" -gt 0 ]; then
		if [ "${DRY_RUN}" = "1" ]; then
			warn "${problems} problem(s) - would abort outside a dry run."
		else
			die "${problems} problem(s) above. Fix them, or re-run with --dry-run to inspect."
		fi
	fi
}

run_quiet() { "$@" >/dev/null 2>&1; }

# Verifies every package exists before installing any of them. Debian and Raspberry Pi OS
# disagree about several of these (chromium lives in the Raspberry Pi archive, the "pix"
# boot splash is rpd-plym-splash, Compose v2 is not packaged at all), and discovering that
# mid-install leaves a half-configured machine.
apt_install() {
	local missing=() pkg candidate
	for pkg in "$@"; do
		# "Candidate:" is the honest test - apt-cache policy prints a stanza for unknown
		# packages too, and an unavailable one shows "(none)".
		candidate="$(apt-cache policy "${pkg}" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
		if [ -z "${candidate}" ] || [ "${candidate}" = "(none)" ]; then
			missing+=("${pkg}")
		fi
	done
	if [ "${#missing[@]}" -gt 0 ]; then
		if [ "${DRY_RUN}" = "1" ]; then
			# A dry run never refreshed the package lists, and is usually not even on the
			# target hardware - unavailability here says nothing.
			warn "not offered by this machine's archives (expected in a dry run): ${missing[*]}"
		else
			die "These packages do not exist in the configured archives: ${missing[*]}
Check the names against this system's Raspberry Pi OS release before continuing."
		fi
	fi
	run apt-get install -y --no-install-recommends "$@"
}

# The package list, comments and blank lines stripped.
wanted_packages() { grep -vE '^\s*#|^\s*$' "${PACKAGE_LIST}"; }

step_packages() {
	local packages; mapfile -t packages < <(wanted_packages)

	if [ -n "${OFFLINE_DIR}" ]; then
		# A file:// archive rather than plain `dpkg -i`, so apt still does the dependency
		# ordering. sourcelist/sourceparts are overridden so this never reaches for the
		# network sources that are configured but unreachable.
		log "Installing ${#packages[@]} packages (plus dependencies) from the bundle"
		local list=/etc/apt/sources.list.d/coffeepy-offline.list
		write_file "${list}" <<-EOF
			deb [trusted=yes] file://${OFFLINE_DIR}/debs ./
		EOF
		run apt-get -o Dir::Etc::sourcelist="${list}" -o Dir::Etc::sourceparts=/dev/null \
			-o APT::Get::List-Cleanup=0 update
		run apt-get -o Dir::Etc::sourcelist="${list}" -o Dir::Etc::sourceparts=/dev/null \
			install -y --no-install-recommends "${packages[@]}"
		# An update bundle's packages are newer versions of what is installed (security
		# updates for an offline device): only this source counts, so only they move.
		run apt-get -o Dir::Etc::sourcelist="${list}" -o Dir::Etc::sourceparts=/dev/null \
			upgrade -y
	else
		log "Refreshing the package lists"
		run apt-get update
		log "Installing ${#packages[@]} packages (plus dependencies)"
		apt_install "${packages[@]}"
	fi

	log "Installing Docker Compose ${COMPOSE_VERSION}"
	local arch asset sha tmp
	arch="$(dpkg --print-architecture)"
	case "${arch}" in
		armhf) asset="armv7";   sha="${COMPOSE_SHA256_armhf}" ;;
		arm64) asset="aarch64"; sha="${COMPOSE_SHA256_arm64}" ;;
		*)
			# A dry run is meant to be readable from any machine, so an architecture with
			# no pinned build is only fatal when actually installing.
			[ "${DRY_RUN}" = "1" ] || die "No pinned Compose build for architecture ${arch}."
			warn "no pinned Compose build for ${arch} - would abort outside a dry run"
			return 0
			;;
	esac
	if docker compose version >/dev/null 2>&1 && [ "${FORCE}" != "1" ]; then
		info "already present: $(docker compose version --short 2>/dev/null)"
	elif [ -n "${OFFLINE_DIR}" ]; then
		echo "${sha}  ${OFFLINE_DIR}/docker-compose" | sha256sum -c - >/dev/null \
			|| die "The bundle's docker-compose does not match the checksum this installer expects."
		run install -d /usr/local/lib/docker/cli-plugins
		run install -m 0755 "${OFFLINE_DIR}/docker-compose" /usr/local/lib/docker/cli-plugins/docker-compose
		info "installed from the bundle"
	elif [ "${DRY_RUN}" = "1" ]; then
		info "[dry-run] download and verify docker-compose-linux-${asset}"
	else
		tmp="$(mktemp)"
		curl -fsSL -o "${tmp}" \
			"https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${asset}"
		echo "${sha}  ${tmp}" | sha256sum -c - >/dev/null || die "Compose download failed its checksum."
		install -d /usr/local/lib/docker/cli-plugins
		install -m 0755 "${tmp}" /usr/local/lib/docker/cli-plugins/docker-compose
		rm -f "${tmp}"
		info "installed $(docker compose version --short)"
	fi

	run systemctl enable --now docker
	# Checked here rather than left to the images step, which would retry a missing client
	# three times and then blame the network.
	if [ "${DRY_RUN}" != "1" ]; then
		docker compose version >/dev/null 2>&1 \
			|| die "docker compose does not run after the install - check docker-cli and the plugin."
	fi
}

step_app() {
	log "Installing the application into ${TARGET_DIR}"
	run install -d "${TARGET_DIR}"
	# The same filter the image build used: never carry a developer's .env onto a device,
	# and leave virtualenvs, editor state and build leftovers behind.
	run rsync -a --delete-after --filter=". ${SCRIPT_DIR}/src-filter.txt" \
		"${PROJECT_DIR}/" "${TARGET_DIR}/"

	if [ -f "${TARGET_DIR}/.env" ]; then
		info ".env already exists - keeping it (delete it to have a new one generated)"
		return 0
	fi

	log "Generating ${TARGET_DIR}/.env with per-device secrets"
	if [ "${DRY_RUN}" = "1" ]; then
		info "[dry-run] copy .env.example and fill in fresh secrets"
		return 0
	fi
	cp "${TARGET_DIR}/.env.example" "${TARGET_DIR}/.env"
	chmod 0600 "${TARGET_DIR}/.env"
	sed -i "s/^DJANGO_SECRET_KEY=.*/DJANGO_SECRET_KEY=$(random_token 50)/" "${TARGET_DIR}/.env"
	sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(random_token 24)/" "${TARGET_DIR}/.env"
	sed -i "s/^AP_PASSPHRASE=.*/AP_PASSPHRASE=$(random_token 12)/" "${TARGET_DIR}/.env"
	sed -i "s/^DJANGO_SUPERUSER_PASSWORD=.*/DJANGO_SUPERUSER_PASSWORD=$(random_token 16)/" "${TARGET_DIR}/.env"
	if [ "${SKIP_AP}" != "1" ]; then
		# Pin the address QR codes use to the AP's own fixed IP: autodetection runs inside
		# a container and cannot tell the AP interface from ethernet when both are up.
		sed -i "s/^PUBLIC_HOST=.*/PUBLIC_HOST=${AP_ADDRESS}/" "${TARGET_DIR}/.env"
	fi
}

boot_config() {
	# Bookworm and later put the firmware config on /boot/firmware; older releases on /boot.
	if [ -d /boot/firmware ]; then echo /boot/firmware; else echo /boot; fi
}

step_hardware() {
	local boot; boot="$(boot_config)"

	log "Enabling the DS3231 real-time clock"
	# i2c_arm explicitly, so /dev/i2c-1 exists and `i2cdetect -y 1` can confirm the chip is
	# actually wired up - the overlay alone brings the bus up only for its own use.
	ensure_line "${boot}/config.txt" "dtparam=i2c_arm=on"
	ensure_line "${boot}/config.txt" "dtoverlay=i2c-rtc,ds3231"
	ensure_line /etc/modules "i2c-dev"
	run systemctl disable fake-hwclock 2>/dev/null || true

	log "Keeping the kernel NFC stack off the card reader"
	# pn533_usb claims the ACR122U the moment it is plugged in and pcscd then finds
	# nothing - the reader looks dead with its LED lit.
	run install -m 0644 "${SCRIPT_DIR}/blacklist-coffeepy-nfc.conf" /etc/modprobe.d/
	run modprobe -r pn533_usb pn533 nfc 2>/dev/null || true

	log "Installing the udev rules"
	run install -m 0644 "${SCRIPT_DIR}/99-acr122u.rules" /etc/udev/rules.d/
	# The backup stick is no longer mounted by udev but by coffeepy-storage.service, which
	# checks it really is this device's stick first (install/storage_helper.py).
	run rm -f /etc/udev/rules.d/99-usb-backup-mount.rules
	run install -d /srv/coffeepy-usb
	run udevadm control --reload-rules

	if [ -n "${VIDEO_MODE}" ]; then
		log "Pinning the display mode"
		if grep -q "video=HDMI" "${boot}/cmdline.txt" 2>/dev/null; then
			info "a video= mode is already set - leaving it alone"
		else
			backup_once "${boot}/cmdline.txt"
			# cmdline.txt is a single line; this appends to it rather than adding one.
			run sed -i "s|\$| ${VIDEO_MODE}|" "${boot}/cmdline.txt"
			info "${VIDEO_MODE}"
		fi
	fi

	log "Trimming what a kiosk does not need"
	# No Bluetooth peripherals, no physical hotkeys, no modem on this device - and on a
	# 1GB board the RAM matters more than the convenience.
	run systemctl disable bluetooth hciuart triggerhappy ModemManager 2>/dev/null || true
	ensure_line "${boot}/config.txt" "dtoverlay=disable-bt"

	log "Growing swap (crash safety net, not something to lean on)"
	if [ -f /etc/dphys-swapfile ]; then
		backup_once /etc/dphys-swapfile
		run sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
	else
		info "dphys-swapfile not installed - skipping"
	fi
}

step_kiosk() {
	local kiosk_user="${KIOSK_USER:-${SUDO_USER:-pi}}"
	if ! id "${kiosk_user}" >/dev/null 2>&1; then
		# Fatal for a real install - the kiosk session has to belong to somebody - but a
		# dry run should still be able to show the remaining plan.
		[ "${DRY_RUN}" = "1" ] || die "No such user: ${kiosk_user}. Set KIOSK_USER=<name> and re-run."
		warn "no such user ${kiosk_user} - would abort outside a dry run; skipping this step"
		return 0
	fi
	local home; home="$(getent passwd "${kiosk_user}" | cut -d: -f6)"
	log "Setting up the kiosk session for ${kiosk_user} (${home})"

	run install -d -o "${kiosk_user}" -g "${kiosk_user}" "${home}/.config/openbox"
	run install -m 0755 -o "${kiosk_user}" -g "${kiosk_user}" \
		"${SCRIPT_DIR}/kiosk/openbox-autostart" "${home}/.config/openbox/autostart"
	run install -m 0644 -o "${kiosk_user}" -g "${kiosk_user}" \
		"${SCRIPT_DIR}/kiosk/xinitrc" "${home}/.xinitrc"

	# Appended to .profile, not written over it: the account's normal PATH and .bashrc
	# sourcing has to keep working.
	if ! grep -q "CoffeePy kiosk autostart" "${home}/.profile" 2>/dev/null; then
		if [ "${DRY_RUN}" = "1" ]; then
			info "[dry-run] append the startx snippet to ${home}/.profile"
		else
			{
				echo ""
				echo "# --- CoffeePy kiosk autostart ---"
				cat "${SCRIPT_DIR}/kiosk/bash_profile_kiosk"
			} >> "${home}/.profile"
			chown "${kiosk_user}:${kiosk_user}" "${home}/.profile"
		fi
	fi

	# A Desktop image (instead of the documented Lite) autologins into a full desktop through
	# lightdm on its own VT, next to the kiosk's X on tty1 - harmless to look at, but on a
	# 1GB board it costs the RAM the containers need.
	if systemctl is-enabled display-manager.service >/dev/null 2>&1; then
		log "Disabling the desktop's display manager"
		run systemctl disable display-manager.service
	fi
	run systemctl set-default multi-user.target

	log "Locking down the kiosk browser"
	# Managed policies (kiosk/chromium-policies.json - JSON, hence the explanation here):
	# no password manager, so an admin who logs into the dashboard on the touchscreen is
	# never offered to leave the password in the kiosk's profile; no leak check, which
	# would try to reach Google; no translation bar over a German page in an English
	# browser; and no address/card autofill, which would otherwise show
	# the previous person's entries under the nickname and amount fields.
	run install -d /etc/chromium/policies/managed
	run install -m 0644 "${SCRIPT_DIR}/kiosk/chromium-policies.json" /etc/chromium/policies/managed/coffeepy.json

	log "Enabling autologin on tty1"
	run install -d /etc/systemd/system/getty@tty1.service.d
	if [ "${DRY_RUN}" = "1" ]; then
		info "[dry-run] write the agetty override for ${kiosk_user}"
	else
		sed "s/__FIRST_USER_NAME__/${kiosk_user}/" "${SCRIPT_DIR}/kiosk/getty-autologin.conf" \
			> /etc/systemd/system/getty@tty1.service.d/autologin.conf
	fi
	# A fresh install reboots anyway, but a re-run with --only=kiosk otherwise left systemd
	# warning that the getty unit changed on disk.
	run systemctl daemon-reload

	log "Installing the boot splash"
	backup_once /usr/share/plymouth/themes/pix/splash.png
	run install -m 0644 "${SCRIPT_DIR}/kiosk/splash.png" /usr/share/plymouth/themes/pix/splash.png
	local boot; boot="$(boot_config)"
	ensure_line "${boot}/config.txt" "disable_splash=1"
	if ! grep -q "splash" "${boot}/cmdline.txt" 2>/dev/null; then
		backup_once "${boot}/cmdline.txt"
		run sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles/' "${boot}/cmdline.txt"
	fi
	run plymouth-set-default-theme -R pix
}

step_services() {
	log "Installing the systemd units"
	local unit
	for unit in coffeepy.service coffeepy-update-watcher.service coffeepy-storage.service \
	            coffeepy-rtc-sync.service coffeepy-rtc-sync.timer; do
		if [ "${DRY_RUN}" = "1" ]; then
			info "[dry-run] install ${unit} (paths pointing at ${TARGET_DIR})"
		else
			sed "s#/opt/coffeepy#${TARGET_DIR}#g" "${SCRIPT_DIR}/${unit}" \
				> "/etc/systemd/system/${unit}"
		fi
	done
	run systemctl daemon-reload
	run systemctl enable coffeepy.service coffeepy-update-watcher.service
	# What update bundles are checked against (install/update_watcher.py) - outside data/,
	# root's, so nothing running in a container can swap it for its own.
	run install -d -m 0755 /etc/coffeepy
	run install -m 0644 "${SCRIPT_DIR}/update-signing.pub" /etc/coffeepy/update-signing.pub
	# The watcher runs this step itself after an update - restarting it then would cut the
	# update short; it hands over to its new version on its own once it is done.
	[ "${COFFEEPY_FROM_WATCHER:-0}" = "1" ] || run systemctl restart coffeepy-update-watcher.service
	run systemctl enable --now coffeepy-storage.service
	run systemctl restart coffeepy-storage.service  # a re-run brings a changed helper in
	run systemctl enable --now coffeepy-rtc-sync.timer

	if [ "${DRY_RUN}" != "1" ]; then
		info "RTC check: $(hwclock --show 2>&1 | head -1 || true)"
	fi
}

step_images() {
	if [ -n "${OFFLINE_DIR}" ]; then
		log "Loading the app images from the bundle"
		run docker load -i "${OFFLINE_DIR}/images.tar"
		return 0
	fi
	log "Building the app images - the long part, especially on a Pi 3"
	if [ "${DRY_RUN}" = "1" ]; then
		info "[dry-run] docker compose build && docker compose pull --ignore-buildable"
		return 0
	fi
	local attempt
	for attempt in 1 2 3; do
		# Every image the compose file uses but does not build (db, mosquitto, caddy) - a
		# hand-kept list here is how a new service ends up missing on the device.
		if (cd "${TARGET_DIR}" && docker compose build && docker compose pull --ignore-buildable); then
			return 0
		fi
		warn "Build attempt ${attempt} failed."
		[ "${attempt}" -lt 3 ] && { info "retrying in 15s (a flaky mirror is the usual cause)"; sleep 15; }
	done
	die "Could not build the app images. The installer is resumable: fix the network and run it again."
}

step_ap() {
	if [ "${SKIP_AP}" = "1" ]; then
		info "--skip-ap: leaving networking as it is"
		return 0
	fi
	log "Configuring the standalone WiFi access point"
	warn "This takes the Pi off your network and onto its own AP at ${AP_ADDRESS}."
	warn "An SSH session over WiFi will be cut; over ethernet it survives."
	confirm "Configure the access point now?" || { info "skipped - re-run with --only=ap when ready"; return 0; }

	# Tolerant on purpose: with pipefail a missing .env would otherwise abort the step,
	# and --only=ap is a legitimate way to run this before the app step ever ran.
	local ssid passphrase
	ssid="$(grep -E '^AP_SSID=' "${TARGET_DIR}/.env" 2>/dev/null | cut -d= -f2- || true)"
	passphrase="$(grep -E '^AP_PASSPHRASE=' "${TARGET_DIR}/.env" 2>/dev/null | cut -d= -f2- || true)"
	if [ -z "${ssid}" ] || [ -z "${passphrase}" ]; then
		warn "No AP_SSID/AP_PASSPHRASE in ${TARGET_DIR}/.env - falling back to the template defaults."
	fi

	# Raspberry Pi OS boots with the radio soft-blocked unless WiFi was set up in the Imager,
	# and hostapd then only reports "Could not connect to kernel driver". systemd-rfkill
	# stores the state at shutdown, so unblocking once is enough.
	run rfkill unblock wlan

	# Bookworm and later run NetworkManager, which would keep claiming wlan0 back from
	# hostapd and systemd-networkd. ethernet stays with it. Unmanaged is not enough on its
	# own: NM keeps its own "WiFi disabled" state and re-applies the soft block at every
	# boot regardless, so the radio has to be switched on through NM as well.
	local nm=0
	if systemctl is-enabled NetworkManager >/dev/null 2>&1; then
		nm=1
		write_file /etc/NetworkManager/conf.d/coffeepy-ap.conf <<-EOF
			[keyfile]
			unmanaged-devices=interface-name:wlan0
		EOF
		run systemctl reload NetworkManager
		run nmcli radio wifi on
	fi

	run systemctl unmask hostapd
	if [ "${DRY_RUN}" = "1" ]; then
		info "[dry-run] render hostapd.conf for SSID '${ssid:-CoffeePy}'"
	else
		backup_once /etc/hostapd/hostapd.conf
		install -d /etc/hostapd
		sed -e "s/__AP_SSID__/${ssid:-CoffeePy}/" -e "s/__AP_PASSPHRASE__/${passphrase:-change-me-please}/" \
			"${SCRIPT_DIR}/hostapd.conf.template" > /etc/hostapd/hostapd.conf
		chmod 0600 /etc/hostapd/hostapd.conf
		echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' > /etc/default/hostapd
	fi
	# brcmfmac comes up with power saving on, which costs a phone on the AP latency spikes
	# for nothing on a mains-powered box. Re-applied on every hostapd start, since the
	# interface is reset with it.
	write_file /etc/systemd/system/hostapd.service.d/coffeepy-power-save.conf <<-EOF
		[Service]
		ExecStartPost=-/usr/sbin/iw dev wlan0 set power_save off
	EOF

	backup_once /etc/dnsmasq.conf
	if ! grep -q "coffeepy" /etc/dnsmasq.conf 2>/dev/null; then
		if [ "${DRY_RUN}" = "1" ]; then
			info "[dry-run] append the dnsmasq range for the AP"
		else
			cat "${SCRIPT_DIR}/dnsmasq.conf.template" >> /etc/dnsmasq.conf
		fi
	fi

	write_file /etc/systemd/network/25-wlan0-ap.network <<-EOF
		[Match]
		Name=wlan0

		[Link]
		RequiredForOnline=no

		[Network]
		Address=${AP_ADDRESS}/24
		DHCPServer=no
	EOF

	run systemctl enable --now systemd-networkd
	# Enabling networkd pulls in its wait-online, which then waits for links it does not
	# manage and times out after two minutes - holding back coffeepy.service, which is
	# ordered after network-online.target. NM's own wait-online already covers ethernet.
	[ "${nm}" = "1" ] && run systemctl disable systemd-networkd-wait-online.service
	run systemctl daemon-reload
	run systemctl enable --now hostapd dnsmasq
	# A re-run (--only=ap) finds hostapd already running with the old config.
	run systemctl restart hostapd
}

step_verify() {
	log "Checking the result"
	local failures=0

	if [ "${DRY_RUN}" = "1" ]; then
		info "[dry-run] nothing to verify"
		return 0
	fi

	systemctl is-enabled coffeepy.service >/dev/null 2>&1 \
		&& info "coffeepy.service enabled" || { warn "coffeepy.service is not enabled"; failures=$((failures+1)); }

	if ! systemctl is-active coffeepy.service >/dev/null 2>&1; then
		info "starting the stack ..."
		systemctl start coffeepy.service || true
	fi

	local waited=0
	while [ "${waited}" -lt 120 ]; do
		# Through Caddy's kiosk port, checked against the device CA the stack created.
		if curl -sf --cacert "${TARGET_DIR}/data/tls/ca.crt" https://localhost:8443/health/ >/dev/null 2>&1; then
			info "the app answers on https://localhost:8443/ (and on port 443 for the network)"
			break
		fi
		sleep 5
		waited=$((waited + 5))
	done
	if [ "${waited}" -ge 120 ]; then
		warn "No answer from https://localhost:8443/health/ after two minutes."
		warn "Look at: docker compose -f ${TARGET_DIR}/docker-compose.yml logs"
		failures=$((failures + 1))
	fi

	if [ -e /dev/rtc0 ]; then
		info "RTC: $(hwclock --show 2>&1 | head -1 || true)"
	else
		warn "No /dev/rtc0 - the DS3231 is not wired up (or needs the reboot below)."
	fi

	if lsusb 2>/dev/null | grep -qi "072f:2200\|ACR122"; then
		info "ACR122U reader detected"
	else
		warn "No ACR122U on USB - plug it in, then check the kiosk's reader status."
	fi

	echo
	log "Done."
	info "Kiosk:     https://localhost:8443/kiosk/  (only on this device; on the touchscreen after a reboot)"
	if [ "${SKIP_AP}" != "1" ]; then
		info "From a phone: join the WiFi from ${TARGET_DIR}/.env, then https://${AP_ADDRESS}/"
		info "           (install https://${AP_ADDRESS}/certificate/ once to lose the browser warning)"
	fi
	info "Passwords: ${TARGET_DIR}/.env - read them once, then keep them somewhere safe."
	info "Log:       ${LOG_FILE}"
	echo
	warn "A reboot is required: the RTC overlay, the display mode and the kiosk autologin"
	warn "all only take effect at boot.   sudo reboot"

	[ "${failures}" -eq 0 ]
}

# --------------------------------------------------------------------------- driver

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

for arg in "$@"; do
	case "${arg}" in
		--dry-run) DRY_RUN=1 ;;
		--yes|-y)  ASSUME_YES=1 ;;
		--force)   FORCE=1 ;;
		--skip-ap) SKIP_AP=1 ;;
		--only=*)  ONLY="${arg#--only=}" ;;
		--offline=*) OFFLINE_DIR="${arg#--offline=}" ;;
		-h|--help) usage 0 ;;
		*) echo "Unknown option: ${arg}" >&2; usage 1 ;;
	esac
done

if [ "${DRY_RUN}" != "1" ] && [ "$(id -u)" -eq 0 ]; then
	exec > >(tee -a "${LOG_FILE}") 2>&1
	echo "=== $(date -Is) install.sh $* ==="
fi

# set -E propagates this trap into functions - and into subshells and command
# substitutions, where a failure is usually handled by the caller. Reporting only from the
# main shell keeps one real failure from printing four confusing copies of itself.
on_error() {
	[ "${BASHPID}" = "$$" ] || exit 1
	echo
	die "Failed in step \"${CURRENT_STEP:-?}\" (line $1). Nothing is marked done, so re-running resumes there. Log: ${LOG_FILE}"
}
trap 'on_error ${LINENO}' ERR

# The kiosk shows "back in a moment" while this runs (install/maintenance.sh) - on a re-run
# its stack is up the whole time, being rebuilt and restarted underneath it.
step_weight() {
	case "$1" in
		preflight) echo 3 ;; packages) echo 8 ;; app) echo 30 ;; hardware) echo 33 ;; kiosk) echo 36 ;;
		services) echo 40 ;; images) echo 44 ;; ap) echo 88 ;; verify) echo 92 ;; *) echo 0 ;;
	esac
}
if [ "${DRY_RUN}" != "1" ]; then
	MAINTENANCE_DIR="${TARGET_DIR}/data/maintenance"
	# shellcheck source=maintenance.sh
	. "${SCRIPT_DIR}/maintenance.sh"
	maintenance_begin install
	trap 'maintenance_end' EXIT
fi

CURRENT_STEP=""
for step in ${ALL_STEPS}; do
	[ -n "${ONLY}" ] && [ "${ONLY}" != "${step}" ] && continue
	if [ "${FORCE}" != "1" ] && [ -z "${ONLY}" ] && step_done "${step}"; then
		log "Step ${step}: already done, skipping (--force to repeat)"
		continue
	fi
	CURRENT_STEP="${step}"
	log "Step ${step}"
	[ "${DRY_RUN}" = "1" ] || maintenance_step "${step}" "$(step_weight "${step}")"
	"step_${step}"
	mark_done "${step}"
done
