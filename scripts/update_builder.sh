#!/bin/bash
# CoffeePy update package builder: builds a signed update bundle on *this* machine - and,
# if asked, uploads it straight to a device's dashboard and follows it being installed.
#
#   ./scripts/update_builder.sh          guided, full-screen
#
# It asks what goes in (platform, the device's own services from install/, operating
# system packages for an offline device), which signing key to use (and creates one if
# there is none yet), where the bundle goes - then runs scripts/build_update_bundle.sh and
# shows its progress. For scripts and CI, call build_update_bundle.sh directly.
#
# Needs bash 4+, docker or podman with compose, openssl, curl, git - and for building for
# the Pi on an x86 machine, binfmt/qemu emulation for its architecture. The screens are the
# provisioning tool's (scripts/provision.sh, sourced), in English or German.
set -Euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=provision.sh
. "${SCRIPT_DIR}/provision.sh"

FRAME_TITLE=""
FRAME_STEPS=()
CURRENT_STEP=0
PLATFORM="${PLATFORM:-linux/arm64}"
WITH_INSTALL="${WITH_INSTALL:-yes}"
WITH_OS="${WITH_OS:-no}"
SIGNING_KEY="${COFFEEPY_SIGNING_KEY:-$HOME/.config/coffeepy/update-signing.key}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT}}"
BUILD_LOG="${WORK}/build.log"
BUNDLE=""
DEVICE_HOST="${PI_HOST:-}"
DEVICE_USER="${ADMIN_USER:-admin}"
DEVICE_PASSWORD=""

builder_frame() {
	FRAME_TITLE="$(t "Update package builder" "Update-Paket bauen")"
	FRAME_STEPS=("$(t Contents Inhalt)" "$(t Build Bauen)" "$(t Upload Hochladen)" "$(t Done Fertig)")
	PROGRESS_HINT="$(t "building on this machine - this can take a while" "wird auf diesem Rechner gebaut - das kann dauern")"
}

container_tool() {
	if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo docker
	elif command -v podman >/dev/null 2>&1; then echo podman
	fi
}

yes_no() { [ "$1" = yes ] && t yes ja || t no nein; }

# --------------------------------------------------------------------------- contents

step_contents() {
	CURRENT_STEP=0
	local selected=0
	while true; do
		local key_label
		if [ -f "${SIGNING_KEY}" ]; then key_label="${SIGNING_KEY/#$HOME/~}"; else key_label="$(t "none yet - created on build" "noch keiner - wird beim Bauen erzeugt")"; fi
		local items=(
			"$(t Platform Plattform)|${PLATFORM}"
			"$(t "Device services" "Gerätedienste")|$(yes_no "${WITH_INSTALL}") $(t "(install/)" "(install/)")"
			"$(t "OS packages" "Betriebssystem-Pakete")|$(yes_no "${WITH_OS}")"
			"$(t "Signing key" "Signaturschlüssel")|${key_label}"
			"$(t "Save to" "Speichern in")|${OUTPUT_DIR/#$HOME/~}"
			"---"
			"$(t "Build the update package" "Update-Paket bauen")"
		)
		ui_menu "$(t "What goes into the update" "Was ins Update kommt")" \
			"$(t "The app's images and docker-compose.yml are always in it. Version: " "Die Images der App und docker-compose.yml sind immer dabei. Version: ")$(cd "${ROOT}" && git describe --tags --always --dirty 2>/dev/null || echo ?)" \
			"${selected}" "${items[@]}" || exit 0
		selected=${MENU}
		case "${MENU}" in
			0)
				ui_menu "$(t Platform Plattform)" "$(t "What the device runs: 64-bit Raspberry Pi OS (the default) or 32-bit." "Was auf dem Gerät läuft: 64-Bit-Raspberry-Pi-OS (Standard) oder 32-Bit.")" \
					"$([ "${PLATFORM}" = linux/arm/v7 ] && echo 1 || echo 0)" "linux/arm64|64-Bit" "linux/arm/v7|32-Bit" || continue
				[ "${MENU}" = 1 ] && PLATFORM=linux/arm/v7 || PLATFORM=linux/arm64 ;;
			1)
				if ui_confirm "$(t "Device services" "Gerätedienste")" "$(t "Include install/ - the update watcher, the backup stick service, systemd units, the kiosk session? Leave it out only if none of them changed." \
					"install/ mitliefern - Update-Wächter, Backup-Stick-Dienst, systemd-Units, Kiosk-Sitzung? Nur weglassen, wenn sich daran nichts geändert hat.")" "${WITH_INSTALL}"; then
					WITH_INSTALL=yes; else WITH_INSTALL=no; fi ;;
			2)
				if ui_confirm "$(t "OS packages" "Betriebssystem-Pakete")" "$(t "Include the Raspberry Pi OS packages of install/packages.txt with their dependencies - security updates for a device without internet. Makes the bundle bigger and the build much slower." \
					"Die Raspberry-Pi-OS-Pakete aus install/packages.txt samt Abhängigkeiten mitliefern - Sicherheitsupdates für ein Gerät ohne Internet. Macht das Paket größer und den Bau deutlich langsamer.")" "${WITH_OS}"; then
					WITH_OS=yes; else WITH_OS=no; fi ;;
			3)
				ask SIGNING_KEY "$(t "Signing key" "Signaturschlüssel")" "$(t "The private key the bundle is signed with. Devices only install bundles signed with the key whose public half install/update-signing.pub holds." \
					"Der private Schlüssel, mit dem das Paket signiert wird. Geräte installieren nur Pakete mit dem Schlüssel, dessen öffentliche Hälfte in install/update-signing.pub steht.")" check_nonempty || true
				SIGNING_KEY="${SIGNING_KEY/#\~/$HOME}" ;;
			4)
				ask OUTPUT_DIR "$(t "Save to" "Speichern in")" "$(t "The folder the bundle is written to. Not /tmp - the bundle is several hundred MB." \
					"Der Ordner, in den das Paket geschrieben wird. Nicht /tmp - das Paket hat mehrere hundert MB.")" check_directory || true
				OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}" ;;
			6) return 0 ;;
		esac
	done
}

check_directory() { [ -d "${1/#\~/$HOME}" ] && [ -w "${1/#\~/$HOME}" ] || { t "A folder you can write to." "Ein Ordner, in den du schreiben darfst."; return 1; }; }

ensure_key() {
	[ -f "${SIGNING_KEY}" ] && return 0
	ui_confirm "$(t "Signing key" "Signaturschlüssel")" "$(t "There is no signing key yet. Create one now? Its public half is written to install/update-signing.pub - commit that, and install it on the devices (the installer does) before they accept bundles signed with it." \
		"Es gibt noch keinen Signaturschlüssel. Jetzt einen erzeugen? Seine öffentliche Hälfte wird nach install/update-signing.pub geschrieben - committe sie und bring sie auf die Geräte (das macht der Installer), bevor sie damit signierte Pakete annehmen.")" yes || exit 0
	"${SCRIPT_DIR}/update_signing_key.sh" "${SIGNING_KEY}" >>"${BUILD_LOG}" 2>&1 || fail "$(t "Could not create the key." "Der Schlüssel konnte nicht erzeugt werden.")"
}

# --------------------------------------------------------------------------- build

build_stage() { # the line build_update_bundle.sh printed last -> percent and label
	case "$1" in
		*"building coffeepy-web"*) echo "10|$(t "Building the app images" "Die App-Images werden gebaut")" ;;
		*"Pulling"*) echo "45|$(t "Fetching the other images" "Die übrigen Images werden geholt")" ;;
		*"Saving images"*) echo "60|$(t "Saving the images" "Die Images werden gespeichert")" ;;
		*"Adding install/"*) echo "75|$(t "Adding the device services" "Gerätedienste kommen dazu")" ;;
		*"Collecting Raspberry Pi OS"*) echo "78|$(t "Collecting the OS packages (slow)" "Betriebssystem-Pakete werden gesammelt (dauert)")" ;;
		*"Wrote "*) echo "100|$(t "Done" "Fertig")" ;;
		*) echo "" ;;
	esac
}

step_build() {
	CURRENT_STEP=1
	local tool
	tool="$(container_tool)"
	[ -n "${tool}" ] || fail "$(t "Neither docker nor podman is installed here." "Weder docker noch podman ist hier installiert.")"
	ensure_key
	local version
	version="$(cd "${ROOT}" && git describe --tags --always --dirty 2>/dev/null || echo unversioned)"
	BUNDLE="${OUTPUT_DIR}/coffeepy-update-${version}.tar"
	local args=(--platform "${PLATFORM}" --output "${BUNDLE}" --key "${SIGNING_KEY}")
	[ "${WITH_INSTALL}" = yes ] || args+=(--no-install)
	[ "${WITH_OS}" = yes ] && args+=(--with-os-packages)

	PROGRESS_TITLE="$(t "Building" "Wird gebaut") ${version}"
	PROGRESS_SHOWN=""
	( cd "${ROOT}" && DOCKER="${tool}" scripts/build_update_bundle.sh "${args[@]}" ) >"${BUILD_LOG}" 2>&1 &
	local pid=$! percent=2 label stage last
	label="$(t "Starting" "Startet")"
	while kill -0 "${pid}" 2>/dev/null; do
		last="$(grep -a '^\(== \|Wrote \)' "${BUILD_LOG}" | tail -1 || true)"
		stage="$(build_stage "${last}")"
		[ -n "${stage}" ] && percent=${stage%%|*} label=${stage#*|}
		ui_progress "${percent}" "${label}" "$(tail -c 300 "${BUILD_LOG}" | tr -d '\r' | tail -1 | cut -c1-70)"
		sleep 2
	done
	wait "${pid}" || fail "$(t "The build failed - the last lines of its log:" "Der Bau ist fehlgeschlagen - die letzten Zeilen des Protokolls:")
$(tail -6 "${BUILD_LOG}")"
	ui_progress 100 "$(t Done Fertig)" "$(basename "${BUNDLE}")"
}

# --------------------------------------------------------------------------- upload

step_upload() {
	CURRENT_STEP=2
	ui_confirm "$(t "Upload" "Hochladen")" "$(t "Upload the bundle to a device now? It is installed right away - signature, space and version are checked first, and a failed update is rolled back by itself." \
		"Das Paket jetzt auf ein Gerät hochladen? Es wird gleich installiert - Signatur, Platz und Version werden vorher geprüft, ein misslungenes Update wird von selbst zurückgerollt.")" no || return 0
	ask DEVICE_HOST "$(t "Device" "Gerät")" "$(t "Its address, as in the browser (e.g. 192.168.4.1)." "Seine Adresse wie im Browser (z.B. 192.168.4.1).")" check_host || return 0
	ask DEVICE_USER "$(t "Dashboard admin" "Dashboard-Admin")" "$(t "An admin of the device's dashboard." "Ein Admin des Dashboards auf dem Gerät.")" check_nonempty || return 0
	ask DEVICE_PASSWORD "$(t "Password" "Passwort")" "$(t "Their dashboard password." "Das Dashboard-Passwort.")" check_nonempty secret || return 0

	local base="https://${DEVICE_HOST}" jar="${WORK}/cookies" token
	# The device signs its own certificate; its CA is offered at /certificate/. For this one
	# connection the certificate is not checked - the bundle's signature is what protects it.
	local curl=(curl -sk --max-time 30 -b "${jar}" -c "${jar}")
	PROGRESS_TITLE="$(t "Uploading to" "Hochladen nach") ${DEVICE_HOST}"
	PROGRESS_SHOWN=""
	ui_progress 5 "$(t "Logging in" "Anmelden")" "${DEVICE_USER}@${DEVICE_HOST}"
	"${curl[@]}" -o /dev/null "${base}/dashboard/login/" || fail "$(t "The device does not answer at" "Das Gerät antwortet nicht unter") ${base}"
	token="$(awk '$6 == "csrftoken" {print $7}' "${jar}")"
	"${curl[@]}" -o /dev/null -e "${base}/dashboard/login/" --data-urlencode "csrfmiddlewaretoken=${token}" \
		--data-urlencode "username=${DEVICE_USER}" --data-urlencode "password=${DEVICE_PASSWORD}" "${base}/dashboard/login/"
	grep -q sessionid "${jar}" || fail "$(t "Login failed - user name or password wrong, or the account is locked." "Anmeldung fehlgeschlagen - Benutzername oder Passwort falsch, oder das Konto ist gesperrt.")"
	token="$(awk '$6 == "csrftoken" {print $7}' "${jar}")"
	ui_progress 15 "$(t "Uploading" "Wird hochgeladen")" "$(basename "${BUNDLE}") · $(du -h "${BUNDLE}" | cut -f1)"
	curl -sk --max-time 3600 -b "${jar}" -c "${jar}" -o /dev/null -e "${base}/dashboard/update/" \
		-F "csrfmiddlewaretoken=${token}" \
		-F "bundle=@${BUNDLE}" "${base}/dashboard/update/upload/" \
		|| fail "$(t "The upload broke off." "Das Hochladen ist abgebrochen.")"

	# Follow the device's update watcher until it is done (or rolled back).
	local status state message percent waited=0
	while [ "${waited}" -lt 1800 ]; do
		status="$("${curl[@]}" "${base}/dashboard/update/status/" 2>/dev/null || true)"
		state="$(printf '%s' "${status}" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d["state"]; print(s.get("state",""), "|", s.get("percent",0), "|", s.get("message",""), "|", d.get("pending"))' 2>/dev/null || true)"
		percent="$(echo "${state}" | cut -d'|' -f2 | tr -d ' ')"
		message="$(echo "${state}" | cut -d'|' -f3 | sed 's/^ //;s/ $//')"
		case "${state}" in
			"done "*"False") UPLOAD_RESULT="$(t "Installed" "Installiert"): ${message}"; return 0 ;;
			"rolled_back "*"False"|"failed "*"False") UPLOAD_RESULT="$(t "Not installed" "Nicht installiert"): ${message}"; return 0 ;;
		esac
		ui_progress "${percent:-20}" "$(t "Installing on the device" "Wird auf dem Gerät installiert")" "${message:-$(t "waiting for the device" "warte auf das Gerät")}"
		sleep 3
		waited=$((waited + 3))
	done
	UPLOAD_RESULT="$(t "No result after 30 minutes - see the device's update page." "Nach 30 Minuten kein Ergebnis - sieh auf der Update-Seite des Geräts nach.")"
}

# --------------------------------------------------------------------------- done

step_finish() {
	CURRENT_STEP=3
	local sha size version
	sha="$(sha256sum "${BUNDLE}" | cut -c1-16)"
	size="$(du -h "${BUNDLE}" | cut -f1)"
	version="$(tar -xOf "${BUNDLE}" manifest.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
	ui_message "$(t "Update package ready" "Update-Paket fertig")" "${BUNDLE/#$HOME/~}

$(t Version Version): ${version}
$(t Size Größe): ${size} · SHA-256 ${sha}…
$(t Platform Plattform): ${PLATFORM} · $(t "device services" "Gerätedienste"): $(yes_no "${WITH_INSTALL}") · $(t "OS packages" "Betriebssystem-Pakete"): $(yes_no "${WITH_OS}")
${UPLOAD_RESULT:+
${UPLOAD_RESULT}}
$(t "Upload it on the device's dashboard under System › Update, or with this tool." "Lade es im Dashboard des Geräts unter System › Update hoch - oder mit diesem Werkzeug.")"
}

fail() {
	ui_message "$(t "Something went wrong" "Etwas ist schiefgegangen")" "$1" || true
	exit 1
}

builder_main() {
	[ "${BASH_VERSINFO[0]}" -ge 4 ] || { echo "Needs bash 4 or newer." >&2; exit 1; }
	local missing="" tool
	for tool in openssl curl git tar python3; do command -v "${tool}" >/dev/null || missing+=" ${tool}"; done
	[ -n "${missing}" ] && { echo "Missing on this machine:${missing}" >&2; exit 1; }
	tui_init
	if [ "${TUI_ACTIVE}" = 1 ] && { [ "${COLS}" -lt 80 ] || [ "${ROWS}" -lt 24 ]; }; then
		tui_end; echo "Needs a terminal of at least 80x24 (this one is ${COLS}x${ROWS})." >&2; exit 1
	fi
	if [ -z "${UI_LANG}" ]; then
		case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in de*) UI_LANG=de ;; *) UI_LANG=en ;; esac
	fi
	builder_frame
	step_contents
	step_build
	step_upload
	step_finish
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	builder_main "$@"
fi
