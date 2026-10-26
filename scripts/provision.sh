#!/bin/bash
# CoffeePy provisioning: turns a Raspberry Pi into the coffee kiosk from *this* machine.
#
#   ./scripts/provision.sh                         guided, full-screen
#   ./scripts/provision.sh --answers FILE          pre-fill the answers (KEY=VALUE lines)
#   ./scripts/provision.sh --answers FILE --unattended   no questions at all
#
# All it needs on the Pi is a current Raspberry Pi OS with SSH enabled and a user with
# sudo. It asks for the address and credentials, checks the Pi, asks for the settings
# that end up in /opt/coffeepy/.env, copies this checkout over and runs install/install.sh
# there - in the background, so a dropped connection does not stop it - while showing its
# progress. Run it again later to change settings: it reads the existing .env and keeps
# the database password and secret key that the running installation depends on.
#
# Answer keys (environment variables work too, PI_PASSWORD best that way):
#   UI_LANG DEVICE_LANGUAGE PI_HOST PI_USER PI_PASSWORD ADMIN_USER ADMIN_PASSWORD
#   AP_ENABLE AP_SSID AP_PASSPHRASE PUBLIC_HOST TIME_ZONE
#   BACKUP_INTERVAL_MINUTES BACKUP_DEPTH AUDIT_RETENTION_DAYS GUNICORN_WORKERS DISPLAY_MODE INSTALL_SOURCE
#   OFFLINE_BUNDLE REBOOT SAVE_SUMMARY
# PI_SSH_OPTS adds options to every ssh call, e.g. "-p 2222" or "-i ~/.ssh/pi_key".
#
# Keep this in step with the app: a new .env setting or installer option belongs here as
# well (see CLAUDE.md, "Provisioning tool").
#
# Needs bash 4+, ssh, tar and a terminal of at least 80x24 - no dialog/whiptail: the UI
# is drawn here, in the kiosk's own colours, in English or German.
set -Euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Column widths are counted in characters, which needs a UTF-8 locale - otherwise every
# "ü" and "·" pushes the right edge of a line out by a column.
case "$(locale charmap 2>/dev/null)" in UTF-8) ;; *) export LC_CTYPE=C.UTF-8 ;; esac

UNATTENDED=0
ANSWERS=""
WORK="$(mktemp -d)"
CONTROL="${WORK}/ssh"
LOCAL_LOG="${WORK}/install.log"
CURRENT_STEP=0

# Settings (answers). Empty means "work out a default once the Pi is known".
UI_LANG="${UI_LANG:-}"                  # de | en - this tool's own language
DEVICE_LANGUAGE="${DEVICE_LANGUAGE:-}"  # de | en - the screens' default on the Pi
PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-}"
PI_PASSWORD="${PI_PASSWORD:-}"
read -r -a SSH_EXTRA <<<"${PI_SSH_OPTS:-}"
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
AP_ENABLE="${AP_ENABLE:-}"
AP_SSID="${AP_SSID:-}"
AP_PASSPHRASE="${AP_PASSPHRASE:-}"
PUBLIC_HOST="${PUBLIC_HOST:-}"
TIME_ZONE="${TIME_ZONE:-}"
BACKUP_INTERVAL_MINUTES="${BACKUP_INTERVAL_MINUTES:-}"
BACKUP_DEPTH="${BACKUP_DEPTH:-}"
AUDIT_RETENTION_DAYS="${AUDIT_RETENTION_DAYS:-}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-}"
DISPLAY_MODE="${DISPLAY_MODE:-}"        # panel | auto | a literal video=... string
INSTALL_SOURCE="${INSTALL_SOURCE:-}"    # internet | offline
OFFLINE_BUNDLE="${OFFLINE_BUNDLE:-}"
REBOOT="${REBOOT:-}"
SAVE_SUMMARY="${SAVE_SUMMARY:-}"
ANSWER_KEYS="UI_LANG DEVICE_LANGUAGE PI_HOST PI_USER PI_PASSWORD ADMIN_USER ADMIN_PASSWORD
AP_ENABLE AP_SSID AP_PASSPHRASE PUBLIC_HOST TIME_ZONE
BACKUP_INTERVAL_MINUTES BACKUP_DEPTH AUDIT_RETENTION_DAYS GUNICORN_WORKERS DISPLAY_MODE INSTALL_SOURCE
OFFLINE_BUNDLE REBOOT SAVE_SUMMARY"

# What the Pi told us (inspect_pi) and what an earlier installation left (load_existing).
PI_MODEL="" PI_ARCH="" PI_RELEASE="" PI_RAM_MB=0 PI_FREE_MB=0 PI_ONLINE="" PI_IFACE=""
EXISTING_ENV=""
EXISTING_INSTALL=0

# English first, German second; everything the user reads goes through here.
t() { if [ "${UI_LANG}" = de ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# --------------------------------------------------------------------------- palette
# The kiosk's own colours (static/css/base.css), as 24-bit escapes - or the nearest of the
# 256-colour cube on terminals that don't announce truecolor.

TRUECOLOR=0
case "${COLORTERM:-}" in truecolor|24bit) TRUECOLOR=1 ;; esac

_rgb() { # fg|bg r g b
	local layer=38
	[ "$1" = bg ] && layer=48
	if [ "${TRUECOLOR}" = 1 ]; then
		printf '\e[%s;2;%s;%s;%sm' "${layer}" "$2" "$3" "$4"
	else
		printf '\e[%s;5;%sm' "${layer}" $(( 16 + 36 * (($2 * 5 + 127) / 255) + 6 * (($3 * 5 + 127) / 255) + ($4 * 5 + 127) / 255 ))
	fi
}

R=$'\e[0m'
BOLD=$'\e[1m'
C_CREAM="$(_rgb bg 247 239 230)"
C_CARD="$(_rgb bg 230 212 193)"
C_FIELD="$(_rgb bg 255 252 248)"
C_DARK_BG="$(_rgb bg 59 35 20)"
C_COFFEE_BG="$(_rgb bg 107 59 0)"
F_TEXT="$(_rgb fg 42 28 18)"
F_COFFEE="$(_rgb fg 107 59 0)"
F_LIGHT="$(_rgb fg 169 116 79)"
F_ACCENT="$(_rgb fg 201 138 61)"
F_INVERSE="$(_rgb fg 251 243 234)"
F_DANGER="$(_rgb fg 179 38 30)"

# --------------------------------------------------------------------------- terminal

COLS=80
ROWS=24
TUI_ACTIVE=0

tui_init() {
	[ "${UNATTENDED}" = 1 ] && return 0
	[ -t 0 ] && [ -t 1 ] || { echo "Needs a terminal - or run with --unattended." >&2; exit 1; }
	TUI_ACTIVE=1
	printf '\e[?1049h\e[?25l'   # alternate screen, cursor off
	stty -echo -icanon 2>/dev/null || true
	trap 'tui_size' WINCH
	tui_size
}

tui_end() {
	[ "${TUI_ACTIVE}" = 1 ] || return 0
	TUI_ACTIVE=0
	stty echo icanon 2>/dev/null || true
	printf '%s\e[?25h\e[?1049l' "${R}"
}

tui_size() {
	COLS=$(tput cols 2>/dev/null || echo 80)
	ROWS=$(tput lines 2>/dev/null || echo 24)
}

cleanup() {
	tui_end
	ssh -S "${CONTROL}" -O exit "${PI_USER}@${PI_HOST}" >/dev/null 2>&1 || true
	rm -rf "${WORK}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

at() { printf '\e[%d;%dH' "$1" "$2"; }

# Pads or cuts to exactly $1 visible characters (the text here is plain, no escapes).
fit() {
	local width=$1 text=$2
	if [ "${#text}" -gt "${width}" ]; then
		printf '%s' "${text:0:$((width - 1))}…"
	else
		printf '%s%*s' "${text}" $((width - ${#text})) ""
	fi
}

# The frame every screen shares: the brand bar, the steps, the key hints at the bottom.
frame() {
	local hints=$1
	local steps=("$(t Connect Verbinden)" "$(t Check Prüfen)" "$(t Settings Einstellungen)" \
		"$(t Install Installieren)" "$(t Done Fertig)")
	printf '%s%s\e[2J' "${C_CREAM}" "${F_TEXT}"
	at 1 1
	printf '%s%s%s' "${C_DARK_BG}${F_ACCENT}${BOLD}" "  CoffeePy" "${R}${C_DARK_BG}${F_INVERSE}"
	printf '%s' "$(fit $((COLS - 10)) "  ·  $(t "Raspberry Pi setup" "Raspberry-Pi-Einrichtung")")"
	if [ -n "${PI_HOST}" ]; then
		local target="${PI_USER:+${PI_USER}@}${PI_HOST}  "
		at 1 $((COLS - ${#target} + 1)); printf '%s' "${target}"
	fi
	at 2 1
	printf '%s%*s' "${C_COFFEE_BG}" "${COLS}" ""
	at 2 3
	local i label
	for i in "${!steps[@]}"; do
		label=" $((i + 1)) ${steps[$i]} "
		if [ "$i" -eq "${CURRENT_STEP}" ]; then
			printf '%s%s%s' "${C_CREAM}${F_COFFEE}${BOLD}" "${label}" "${R}${C_COFFEE_BG}"
		elif [ "$i" -lt "${CURRENT_STEP}" ]; then
			printf '%s%s' "${C_COFFEE_BG}${F_INVERSE}" "${label}"
		else
			printf '%s%s' "${C_COFFEE_BG}${F_LIGHT}" "${label}"
		fi
		[ "$i" -lt $((${#steps[@]} - 1)) ] && printf '%s›' "${C_COFFEE_BG}${F_LIGHT}"
	done
	at "${ROWS}" 1
	printf '%s%s' "${C_DARK_BG}${F_LIGHT}" "$(fit "${COLS}" "  ${hints}")"
	printf '%s' "${R}"
}

# Width and left edge of the card, without drawing it - wrap() needs the width before the
# height (and so the card itself) is known.
card_geometry() {
	CARD_W=$(( COLS - 8 < 78 ? COLS - 8 : 78 ))
	CARD_LEFT=$(( (COLS - CARD_W) / 2 + 1 ))
	CARD_IN=$(( CARD_W - 6 ))
}

# A card in the style of the kiosk's info boxes: cream-dark, an ochre bar on the left.
# Sets CARD_TOP/CARD_LEFT/CARD_W/CARD_IN (usable width) for card_line.
card() {
	local title=$1 height=$2
	card_geometry
	local space=$(( ROWS - 5 ))
	[ "${height}" -gt "${space}" ] && height=${space}
	CARD_TOP=$(( 4 + (space - height) / 3 ))
	CARD_H=${height}
	local r
	for ((r = 0; r < height; r++)); do
		at $((CARD_TOP + r)) "${CARD_LEFT}"
		printf '%s▌%s%*s' "${C_CARD}${F_ACCENT}" "${C_CARD}" $((CARD_W - 1)) ""
	done
	card_line 1 "${title}" "${F_COFFEE}${BOLD}"
}

card_line() { # row-in-card text [style]
	[ "$1" -ge "${CARD_H}" ] && return 0
	at $((CARD_TOP + $1)) $((CARD_LEFT + 3))
	printf '%s%s%s%s' "${C_CARD}${F_TEXT}" "${3:-}" "$(fit "${CARD_IN}" "$2")" "${R}"
}

# Wraps text to the card's width; result in WRAPPED (array of lines).
wrap() {
	WRAPPED=()
	local paragraph line
	while IFS= read -r paragraph; do
		if [ -z "${paragraph}" ]; then WRAPPED+=(""); continue; fi
		while IFS= read -r line; do WRAPPED+=("${line% }"); done < <(printf '%s\n' "${paragraph}" | fold -s -w "${CARD_IN}")
	done <<<"$1"
}

# --------------------------------------------------------------------------- keys

read_key() {
	local k rest=""
	KEY="" KEYCHAR=""
	IFS= read -rsn1 k || { KEY=eof; return 0; }
	case "${k}" in
		$'\e')
			IFS= read -rsn2 -t 0.03 rest || true
			case "${rest}" in
				'[A') KEY=up ;; '[B') KEY=down ;; '[C') KEY=right ;; '[D') KEY=left ;;
				'[3') read -rsn1 -t 0.03 _ || true; KEY=other ;;
				'') KEY=esc ;; *) KEY=other ;;
			esac ;;
		'') KEY=enter ;;
		$'\t') KEY=tab ;;
		$'\x7f'|$'\b') KEY=backspace ;;
		$'\x15') KEY=clear ;;   # Ctrl-U
		*) KEY=char; KEYCHAR=${k} ;;
	esac
}

# --------------------------------------------------------------------------- widgets
# Every widget falls back to its default in --unattended mode, so one code path drives
# both the guided run and an automated one.

say() { # unattended progress on stderr
	[ "${UNATTENDED}" = 1 ] && printf '[provision] %s\n' "$*" >&2
	return 0
}

ui_message() { # title text [hints]
	if [ "${UNATTENDED}" = 1 ]; then say "$1: $2"; return 0; fi
	PROGRESS_SHOWN=""
	while true; do
		frame "${3:-$(t "Enter continue" "Enter weiter")}"
		card_geometry
		wrap "$2"
		card "$1" $(( ${#WRAPPED[@]} + 5 ))
		local i
		for i in "${!WRAPPED[@]}"; do card_line $((i + 3)) "${WRAPPED[$i]}"; done
		card_line $(( ${#WRAPPED[@]} + 4 )) "› $(t Continue Weiter)" "${F_COFFEE}${BOLD}"
		read_key
		[ "${KEY}" = enter ] && return 0
	done
}

ui_confirm() { # title text default(yes|no) -> status 0 for yes
	local choice=${3:-yes}
	if [ "${UNATTENDED}" = 1 ]; then say "$1 -> ${choice}"; [ "${choice}" = yes ]; return; fi
	PROGRESS_SHOWN=""
	local yes no
	yes=$(t Yes Ja) no=$(t No Nein)
	while true; do
		frame "$(t "←→ choose · Enter confirm" "←→ wählen · Enter bestätigen")"
		card_geometry
		wrap "$2"
		card "$1" $(( ${#WRAPPED[@]} + 6 ))
		local i
		for i in "${!WRAPPED[@]}"; do card_line $((i + 3)) "${WRAPPED[$i]}"; done
		at $((CARD_TOP + ${#WRAPPED[@]} + 4)) $((CARD_LEFT + 3))
		if [ "${choice}" = yes ]; then
			printf '%s  %s  %s   %s  %s  %s' "${C_COFFEE_BG}${F_INVERSE}${BOLD}" "${yes}" "${R}${C_CARD}" "${C_FIELD}${F_TEXT}" "${no}" "${R}"
		else
			printf '%s  %s  %s   %s  %s  %s' "${C_FIELD}${F_TEXT}" "${yes}" "${R}${C_CARD}" "${C_COFFEE_BG}${F_INVERSE}${BOLD}" "${no}" "${R}"
		fi
		read_key
		case "${KEY}" in
			left|right|tab) [ "${choice}" = yes ] && choice=no || choice=yes ;;
			char) case "${KEYCHAR}" in y|Y|j|J) choice=yes ;; n|N) choice=no ;; esac ;;
			enter) [ "${choice}" = yes ]; return ;;
		esac
	done
}

# ui_input title text default [secret] -> INPUT; status 1 on Esc.
ui_input() {
	local title=$1 text=$2 value=$3 secret=${4:-} error=""
	INPUT=${value}
	[ "${UNATTENDED}" = 1 ] && return 0
	PROGRESS_SHOWN=""
	while true; do
		frame "$(t "type to edit · Ctrl-U clear · Enter accept · Esc back" \
			"tippen zum Ändern · Strg-U leeren · Enter übernehmen · Esc zurück")"
		card_geometry
		wrap "${text}"
		card "${title}" $(( ${#WRAPPED[@]} + 7 ))
		local i shown
		for i in "${!WRAPPED[@]}"; do card_line $((i + 3)) "${WRAPPED[$i]}"; done
		if [ -n "${secret}" ]; then shown=$(printf '%*s' "${#value}" "" | tr ' ' '*'); else shown=${value}; fi
		[ "${#shown}" -ge $((CARD_IN - 2)) ] && shown="…${shown: -$((CARD_IN - 4))}"
		at $((CARD_TOP + ${#WRAPPED[@]} + 4)) $((CARD_LEFT + 3))
		printf '%s %s%s▏%s%*s%s' "${C_FIELD}${F_TEXT}${BOLD}" "${shown}" "${F_ACCENT}" "${F_TEXT}" \
			$((CARD_IN - 3 - ${#shown})) "" "${R}"
		[ -n "${error}" ] && card_line $(( ${#WRAPPED[@]} + 5 )) "${error}" "${F_DANGER}"
		read_key
		case "${KEY}" in
			char) value+=${KEYCHAR}; error="" ;;
			backspace) value=${value%?} ;;
			clear) value="" ;;
			esc) return 1 ;;
			enter)
				if [ -n "${INPUT_CHECK:-}" ] && ! error=$("${INPUT_CHECK}" "${value}"); then
					continue
				fi
				INPUT=${value}; return 0 ;;
		esac
	done
}

# ui_menu title text default-index item... -> MENU (index); status 1 on Esc.
# An item "label|value" shows its value right-aligned; "---" is a separator.
ui_menu() {
	local title=$1 text=$2 selected=$3
	shift 3
	local items=("$@")
	MENU=${selected}
	[ "${UNATTENDED}" = 1 ] && return 0
	PROGRESS_SHOWN=""
	while true; do
		frame "$(t "↑↓ move · Enter select · Esc back" "↑↓ bewegen · Enter auswählen · Esc zurück")"
		card_geometry
		wrap "${text}"
		local head=$(( ${#WRAPPED[@]} + 3 ))
		card "${title}" $(( head + ${#items[@]} + 2 ))
		local i label value line
		for i in "${!WRAPPED[@]}"; do card_line $((i + 3)) "${WRAPPED[$i]}"; done
		for i in "${!items[@]}"; do
			if [ "${items[$i]}" = "---" ]; then
				card_line $((head + i)) "$(printf '%*s' "${CARD_IN}" '' | tr ' ' '-')" "${F_LIGHT}"
				continue
			fi
			label=${items[$i]%%|*}
			value=""
			[[ "${items[$i]}" == *"|"* ]] && value=${items[$i]#*|}
			line="$(fit 26 "${label}")${value}"
			at $((CARD_TOP + head + i)) $((CARD_LEFT + 3))
			if [ "$i" -eq "${selected}" ]; then
				printf '%s %s%s' "${C_COFFEE_BG}${F_INVERSE}${BOLD}" "$(fit $((CARD_IN - 1)) "› ${line}")" "${R}"
			else
				printf '%s %s%s' "${C_CARD}${F_TEXT}" "$(fit $((CARD_IN - 1)) "  ${line}")" "${R}"
			fi
		done
		read_key
		case "${KEY}" in
			up)   while :; do selected=$(( (selected - 1 + ${#items[@]}) % ${#items[@]} )); [ "${items[$selected]}" != "---" ] && break; done ;;
			down) while :; do selected=$(( (selected + 1) % ${#items[@]} )); [ "${items[$selected]}" != "---" ] && break; done ;;
			enter) MENU=${selected}; return 0 ;;
			esc) return 1 ;;
		esac
	done
}

# The progress card is drawn once and then only its changing lines are rewritten - a full
# redraw every two seconds made the whole screen flicker.
PROGRESS_TITLE=""
PROGRESS_SHOWN=""
ui_progress() { # percent headline detail
	if [ "${UNATTENDED}" = 1 ]; then
		[ "$2" != "${PROGRESS_LAST:-}" ] && say "$1% $2"
		PROGRESS_LAST=$2
		return 0
	fi
	local key="${PROGRESS_TITLE}:${COLS}x${ROWS}"
	if [ "${PROGRESS_SHOWN}" != "${key}" ]; then
		frame "$(t "the installation runs on the Pi itself - closing this does not stop it" \
			"die Installation läuft auf dem Pi selbst - dieses Fenster zu schließen hält sie nicht an")"
		card "${PROGRESS_TITLE}" 9
		PROGRESS_SHOWN=${key}
	fi
	card_line 3 "$2" "${BOLD}"
	local width=${CARD_IN} filled
	filled=$(( width * $1 / 100 ))
	at $((CARD_TOP + 5)) $((CARD_LEFT + 3))
	printf '%s%*s%s%*s%s' "${C_COFFEE_BG}" "${filled}" "" "${C_FIELD}" $((width - filled)) "" "${R}"
	card_line 6 "$1%" "${F_COFFEE}${BOLD}"
	card_line 7 "$3" "${F_LIGHT}"
}

# --------------------------------------------------------------------------- helpers

# Same trick as install.sh: head closing the pipe is not a failure here.
rand_token() {
	local token
	token="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c "$1" || true)"
	printf '%s' "${token}"
}

# Validators for ui_input (INPUT_CHECK): print the problem and fail, or stay silent.
check_host() { [[ "$1" =~ ^[A-Za-z0-9.:-]+$ ]] || { t "An IP address or hostname, please." "Bitte eine IP-Adresse oder einen Hostnamen."; return 1; }; }
check_user() { [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { t "A Linux user name (lowercase letters, digits, - and _)." "Ein Linux-Benutzername (Kleinbuchstaben, Ziffern, - und _)."; return 1; }; }
check_nonempty() { [ -n "$1" ] || { t "This can't be empty." "Das darf nicht leer sein."; return 1; }; }
# .env values end up unquoted in docker compose and in install.sh's sed into hostapd.conf -
# so no spaces, quotes, $, #, / or &.
check_secret() {
	[ "${#1}" -ge 8 ] || { t "At least 8 characters." "Mindestens 8 Zeichen."; return 1; }
	[[ "$1" =~ ^[A-Za-z0-9_.,:+!?@%=-]+$ ]] || { t "Letters, digits and _ . , : + ! ? @ % = - only." "Nur Buchstaben, Ziffern und _ . , : + ! ? @ % = -"; return 1; }
}
check_passphrase() { check_secret "$1" || return 1; [ "${#1}" -le 63 ] || { t "WPA2 allows 63 characters at most." "WPA2 erlaubt höchstens 63 Zeichen."; return 1; }; }
check_ssid() { [[ "$1" =~ ^[A-Za-z0-9_.-]{1,32}$ ]] || { t "1-32 letters, digits, _ . or -." "1-32 Buchstaben, Ziffern, _ . oder -."; return 1; }; }
check_number() { [[ "$1" =~ ^[1-9][0-9]{0,4}$ ]] || { t "A positive whole number." "Eine positive ganze Zahl."; return 1; }; }
check_tz() { [[ "$1" =~ ^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$ ]] || { t "Like Europe/Berlin." "Zum Beispiel Europe/Berlin."; return 1; }; }
check_bundle() { [ -f "$1" ] || { t "No such file." "Diese Datei gibt es nicht."; return 1; }; }

ask() { # VAR title text check [secret] - re-asks until valid; status 1 on Esc
	local var=$1 title=$2 text=$3 check=$4 secret=${5:-} problem
	INPUT_CHECK=${check}
	if ! ui_input "${title}" "${text}" "${!var}" "${secret}"; then INPUT_CHECK=""; return 1; fi
	INPUT_CHECK=""
	if [ "${UNATTENDED}" = 1 ] && ! problem=$("${check}" "${INPUT}"); then
		echo "${var}: ${problem}" >&2; exit 2
	fi
	printf -v "${var}" '%s' "${INPUT}"
}

language_name() { [ "$1" = de ] && echo Deutsch || echo English; }

# --------------------------------------------------------------------------- the Pi

ssh_base() {
	ssh "${SSH_EXTRA[@]}" -S "${CONTROL}" -o ControlMaster=no -o BatchMode=yes -o ConnectTimeout=10 \
		-o ServerAliveInterval=15 "${PI_USER}@${PI_HOST}" "$@"
}

# One authenticated connection, reused by everything after it: the password is typed
# into ssh exactly once (through SSH_ASKPASS, from a file only this user can read) and
# never shows up on a command line.
pi_connect() {
	local askpass="${WORK}/askpass" secret="${WORK}/secret" out
	ssh -S "${CONTROL}" -O exit "${PI_USER}@${PI_HOST}" >/dev/null 2>&1 || true
	( umask 077; printf '%s\n' "${PI_PASSWORD}" > "${secret}"; printf '#!/bin/sh\ncat %q\n' "${secret}" > "${askpass}" )
	chmod 700 "${askpass}"
	out=$(SSH_ASKPASS="${askpass}" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" \
		ssh "${SSH_EXTRA[@]}" -fN -M -S "${CONTROL}" -o ControlPersist=yes -o ConnectTimeout=10 \
		-o StrictHostKeyChecking=accept-new -o NumberOfPasswordPrompts=1 \
		"${PI_USER}@${PI_HOST}" </dev/null 2>&1) && return 0
	CONNECT_ERROR=${out}
	return 1
}

pi() { ssh_base "$@"; }

# sudo on the Pi, fed the SSH password - the Raspberry Pi OS default user needs none, an
# Imager-created one usually does.
pi_sudo() {
	printf '%s\n' "${PI_PASSWORD}" | ssh_base "sudo -S -p '' $*"
}

step_language() {
	if [ -z "${UI_LANG}" ]; then
		case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in de*) UI_LANG=de ;; *) UI_LANG=en ;; esac
		local current=1
		[ "${UI_LANG}" = de ] && current=0
		ui_menu "Sprache · Language" "Sprache dieses Assistenten - und Vorschlag für die Geräte. / Language of this guide - and the suggestion for the device." \
			"${current}" "Deutsch" "English" || true
		[ "${MENU}" = 0 ] && UI_LANG=de || UI_LANG=en
	fi
	: "${DEVICE_LANGUAGE:=${UI_LANG}}"
}

step_connect() {
	CURRENT_STEP=0
	ui_message "$(t Welcome Willkommen)" "$(t \
"This sets up CoffeePy on a Raspberry Pi: the coffee kiosk on its touchscreen, the NFC card reader, the clock, HTTPS and - if you like - its own WiFi.

Have ready: a Raspberry Pi with a current Raspberry Pi OS, reachable over SSH from here, and the user name and password you gave it in Raspberry Pi Imager. The Pi needs internet for the installation unless you bring an offline bundle.

It takes 20-40 minutes, mostly building the app on the Pi. Nothing on this machine is changed." \
"Hiermit wird CoffeePy auf einem Raspberry Pi eingerichtet: der Kaffee-Kiosk auf dem Touchscreen, der NFC-Kartenleser, die Uhr, HTTPS und - auf Wunsch - ein eigenes WLAN.

Bereithalten: einen Raspberry Pi mit aktuellem Raspberry Pi OS, von hier per SSH erreichbar, und den Benutzernamen und das Passwort aus dem Raspberry Pi Imager. Der Pi braucht für die Installation Internet, außer du bringst ein Offline-Paket mit.

Das dauert 20-40 Minuten, vor allem für den Bau der App auf dem Pi. An diesem Rechner wird nichts verändert.")"
	while true; do
		ask PI_HOST "Raspberry Pi" "$(t "IP address or hostname of the Pi (as shown by your router, or raspberrypi.local)." \
			"IP-Adresse oder Hostname des Pi (wie im Router angezeigt, oder raspberrypi.local).")" check_host || continue
		ask PI_USER "$(t "SSH user" "SSH-Benutzer")" "$(t "The user you created in Raspberry Pi Imager." \
			"Der Benutzer, den du im Raspberry Pi Imager angelegt hast.")" check_user || continue
		ask PI_PASSWORD "$(t Password Passwort)" "$(t "Password of ${PI_USER} on ${PI_HOST} - used for SSH and sudo, kept in memory only." \
			"Passwort von ${PI_USER} auf ${PI_HOST} - für SSH und sudo, wird nur im Speicher gehalten.")" check_nonempty secret || continue
		PROGRESS_TITLE=$(t Connecting Verbinden)
		ui_progress 30 "$(t "Connecting to" "Verbinde mit") ${PI_USER}@${PI_HOST} ..." ""
		if pi_connect; then
			if pi_sudo true >/dev/null 2>&1; then return 0; fi
			ui_message "$(t "No sudo" "Kein sudo")" "$(t "Logged in, but ${PI_USER} can't use sudo with that password. CoffeePy's installer needs it." \
				"Angemeldet, aber ${PI_USER} kann mit diesem Passwort kein sudo verwenden. Der Installer von CoffeePy braucht das.")"
		elif [[ "${CONNECT_ERROR}" == *"REMOTE HOST IDENTIFICATION HAS CHANGED"* ]]; then
			if ui_confirm "$(t "New host key" "Neuer Host-Schlüssel")" "$(t \
				"${PI_HOST} identifies itself with a different key than last time - expected after re-flashing the SD card, alarming otherwise. Forget the old key and connect anyway?" \
				"${PI_HOST} weist sich mit einem anderen Schlüssel aus als beim letzten Mal - nach dem Neubespielen der SD-Karte normal, sonst ein Alarmzeichen. Alten Schlüssel vergessen und trotzdem verbinden?")" no; then
				ssh-keygen -R "${PI_HOST}" >/dev/null 2>&1
				continue
			fi
		else
			ui_message "$(t "Could not connect" "Keine Verbindung")" "$(printf '%s' "${CONNECT_ERROR}" | grep -v '^Warning: Permanently added' | tail -n 3)"
		fi
		[ "${UNATTENDED}" = 1 ] && exit 1
		PI_PASSWORD=""
	done
}

inspect_pi() {
	CURRENT_STEP=1
	local facts
	# The interface is the one the SSH connection came in on - works for a hostname too.
	facts=$(pi 'bash -s' <<-'EOF'
		tr -d "\0" < /proc/device-tree/model 2>/dev/null; echo
		dpkg --print-architecture
		. /etc/os-release; echo "${VERSION_CODENAME:-$ID}"
		awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo
		df --output=avail -k / | tail -1 | awk '{print int($1/1024)}'
		getent hosts deb.debian.org >/dev/null 2>&1 && echo yes || echo no
		set -- ${SSH_CONNECTION}
		ip -o addr show | awk -v a="$3" 'index($4, a "/") == 1 {print $2}' | head -1
	EOF
	) || true
	{ read -r PI_MODEL; read -r PI_ARCH; read -r PI_RELEASE; read -r PI_RAM_MB; read -r PI_FREE_MB; read -r PI_ONLINE; read -r PI_IFACE; } <<<"${facts}"
	PI_RAM_MB=${PI_RAM_MB:-0} PI_FREE_MB=${PI_FREE_MB:-0}

	local warnings=""
	[[ "${PI_MODEL}" == *"Raspberry Pi"* ]] || warnings+="- $(t "This does not look like a Raspberry Pi" "Das sieht nicht nach einem Raspberry Pi aus") (${PI_MODEL:-?}).
"
	case "${PI_ARCH}" in armhf|arm64) ;; *) warnings+="- $(t "Unsupported architecture" "Nicht unterstützte Architektur") ${PI_ARCH:-?}.
" ;; esac
	[ "${PI_RAM_MB}" -lt 900 ] && warnings+="- $(t "Less than 1 GB of RAM - not enough for the app and the kiosk." "Weniger als 1 GB RAM - zu wenig für App und Kiosk.")
"
	[ "${PI_FREE_MB}" -lt 6000 ] && warnings+="- $(t "Less than 6 GB free on the SD card." "Weniger als 6 GB frei auf der SD-Karte.")
"
	[ "${PI_ONLINE}" != yes ] && warnings+="- $(t "No internet on the Pi - you will need an offline bundle (Settings › Install from)." "Kein Internet auf dem Pi - du brauchst ein Offline-Paket (Einstellungen › Installieren aus).")
"
	[[ "${PI_IFACE}" == wlan* ]] && warnings+="- $(t \
"You are connected over the Pi's WiFi. Its own access point takes that WiFi over at the very end and this connection drops - use ethernet if you can, or turn the access point off." \
"Du bist über das WLAN des Pi verbunden. Sein eigener Access Point übernimmt dieses WLAN ganz am Ende und die Verbindung bricht ab - nutze wenn möglich ein Netzwerkkabel oder schalte den Access Point aus.")
"
	local online
	online=$([ "${PI_ONLINE}" = yes ] && t yes ja || t no nein)
	local summary
	summary="$(t "Model:        " "Modell:       ")${PI_MODEL:-?}
System:       Raspberry Pi OS ${PI_RELEASE:-?} (${PI_ARCH:-?})
$(t "Memory:       " "Speicher:     ")${PI_RAM_MB} MB RAM, $((PI_FREE_MB / 1024)) GB $(t "free on the SD card" "frei auf der SD-Karte")
Internet:     ${online}
$(t "Connected via " "Verbunden über ")${PI_IFACE:-?}"
	if [ -n "${warnings}" ]; then
		ui_confirm "$(t "Check the Pi" "Pi prüfen")" "${summary}

$(t "Worth knowing:" "Gut zu wissen:")
${warnings}
$(t "Continue anyway?" "Trotzdem fortfahren?")" no || exit 1
	else
		ui_message "$(t "Check the Pi" "Pi prüfen")" "${summary}

$(t "Looks good." "Sieht gut aus.")"
	fi
}

# Values of an earlier installation's .env become the defaults - and its secrets are kept,
# since the existing database was created with that password.
load_existing() {
	EXISTING_ENV=$(pi_sudo cat /opt/coffeepy/.env 2>/dev/null) || EXISTING_ENV=""
	pi_sudo test -f /var/lib/coffeepy/install-state >/dev/null 2>&1 && EXISTING_INSTALL=1
	[ -n "${EXISTING_ENV}" ] || return 0
	env_get() { printf '%s\n' "${EXISTING_ENV}" | sed -n "s/^$1=//p" | tail -1; }
	: "${ADMIN_USER:=$(env_get DJANGO_SUPERUSER_USERNAME)}"
	# The account already exists with this password - a new random default would only make
	# .env (and the summary at the end) claim one that was never set.
	: "${ADMIN_PASSWORD:=$(env_get DJANGO_SUPERUSER_PASSWORD)}"
	: "${AP_SSID:=$(env_get AP_SSID)}"
	: "${AP_PASSPHRASE:=$(env_get AP_PASSPHRASE)}"
	: "${PUBLIC_HOST:=$(env_get PUBLIC_HOST)}"
	: "${TIME_ZONE:=$(env_get DJANGO_TIME_ZONE)}"
	: "${BACKUP_INTERVAL_MINUTES:=$(env_get BACKUP_INTERVAL_MINUTES)}"
	: "${BACKUP_DEPTH:=$(env_get BACKUP_DEPTH)}"
	: "${AUDIT_RETENTION_DAYS:=$(env_get AUDIT_RETENTION_DAYS)}"
	: "${GUNICORN_WORKERS:=$(env_get GUNICORN_WORKERS)}"
	local existing_language
	existing_language=$(env_get DJANGO_LANGUAGE_CODE)
	[ -n "${existing_language}" ] && [ -z "${DEVICE_LANGUAGE_GIVEN}" ] && DEVICE_LANGUAGE=${existing_language}
	if [ -z "${AP_ENABLE}" ]; then
		[ -n "$(env_get AP_SSID)" ] && AP_ENABLE=yes || AP_ENABLE=no
	fi
	ui_message "$(t "Existing installation" "Bestehende Installation")" "$(t \
"CoffeePy is already installed on this Pi. Its settings are the defaults on the next screen, and its database password and secret key stay as they are.

The admin password in the settings only applies to a newly created account - change an existing one in the dashboard." \
"CoffeePy ist auf diesem Pi schon installiert. Seine Einstellungen sind die Vorgaben auf der nächsten Seite, Datenbankpasswort und Secret Key bleiben unverändert.

Das Admin-Passwort in den Einstellungen gilt nur für ein neu angelegtes Konto - ein bestehendes änderst du im Dashboard.")"
}

defaults() {
	: "${ADMIN_USER:=admin}"
	: "${ADMIN_PASSWORD:=$(rand_token 12)}"
	: "${AP_ENABLE:=$([[ "${PI_IFACE}" == wlan* ]] && echo no || echo yes)}"
	: "${AP_SSID:=CoffeePy}"
	: "${AP_PASSPHRASE:=$(rand_token 12)}"
	: "${TIME_ZONE:=$(timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Berlin)}"
	: "${BACKUP_INTERVAL_MINUTES:=60}"
	: "${BACKUP_DEPTH:=48}"
	: "${AUDIT_RETENTION_DAYS:=90}"
	: "${GUNICORN_WORKERS:=$([ "${PI_RAM_MB}" -ge 1800 ] && echo 3 || echo 2)}"
	: "${DISPLAY_MODE:=panel}"
	: "${INSTALL_SOURCE:=$([ "${PI_ONLINE}" = yes ] && echo internet || echo offline)}"
	: "${REBOOT:=yes}"
	: "${SAVE_SUMMARY:=yes}"
	if [ -z "${PUBLIC_HOST}" ]; then
		[ "${AP_ENABLE}" = yes ] && PUBLIC_HOST=192.168.4.1 || PUBLIC_HOST=${PI_HOST}
	fi
}

display_label() {
	case "${DISPLAY_MODE}" in
		panel) t "1024x600 HDMI panel" "1024x600-HDMI-Panel" ;;
		auto)  t "detected by the display" "vom Bildschirm erkannt" ;;
		*)     echo "${DISPLAY_MODE}" ;;
	esac
}

edit_setting() {
	case "$1" in
	language)
		local current=0
		[ "${DEVICE_LANGUAGE}" = en ] && current=1
		ui_menu "$(t Language Sprache)" "$(t \
"The language kiosk, dashboard and portal start in. Every device can still switch for itself: the kiosk with its language button, phones and PCs in the portal and dashboard." \
"Die Sprache, in der Kiosk, Dashboard und Portal starten. Jedes Gerät kann trotzdem selbst umschalten: der Kiosk über seinen Sprach-Button, Handys und PCs in Portal und Dashboard.")" \
			"${current}" "Deutsch" "English" || return 0
		[ "${MENU}" = 0 ] && DEVICE_LANGUAGE=de || DEVICE_LANGUAGE=en
		;;
	admin)
		ask ADMIN_USER "$(t "Admin account" "Admin-Konto")" "$(t "User name for the dashboard (superuser)." "Benutzername für das Dashboard (Superuser).")" check_user || return 0
		ask ADMIN_PASSWORD "$(t "Admin password" "Admin-Passwort")" "$(t \
			"Password for ${ADMIN_USER} - at least 8 characters. Suggested: a random one, shown again at the end." \
			"Passwort für ${ADMIN_USER} - mindestens 8 Zeichen. Vorgeschlagen: ein zufälliges, das am Ende noch einmal angezeigt wird.")" check_secret || return 0
		;;
	ap)
		if ui_confirm "$(t "WiFi access point" "WLAN-Access-Point")" "$(t \
"Should the Pi open its own WiFi? Phones join it to reach the dashboard and their portal - the device itself never needs another network. Turn it off if the Pi stays on your existing network instead." \
"Soll der Pi ein eigenes WLAN aufspannen? Handys verbinden sich damit, um Dashboard und Portal zu erreichen - das Gerät selbst braucht dann kein anderes Netz. Schalte es aus, wenn der Pi stattdessen in deinem bestehenden Netz bleibt.")" "${AP_ENABLE}"; then
			AP_ENABLE=yes
			ask AP_SSID "$(t "WiFi name" "WLAN-Name")" "$(t "Name (SSID) of the Pi's WiFi." "Name (SSID) des WLANs des Pi.")" check_ssid || return 0
			ask AP_PASSPHRASE "$(t "WiFi password" "WLAN-Passwort")" "$(t "WPA2 password, 8-63 characters. The kiosk shows it as a QR code." \
				"WPA2-Passwort, 8-63 Zeichen. Der Kiosk zeigt es als QR-Code.")" check_passphrase || return 0
			[ "${PUBLIC_HOST}" = "${PI_HOST}" ] && PUBLIC_HOST=192.168.4.1
		else
			AP_ENABLE=no
			[ "${PUBLIC_HOST}" = 192.168.4.1 ] && PUBLIC_HOST=${PI_HOST}
		fi
		;;
	address)
		ask PUBLIC_HOST "$(t "Address for phones" "Adresse für Handys")" "$(t \
"The address phones and PCs use to reach the Pi - in QR codes, and in the HTTPS certificate. With the access point that is 192.168.4.1; on your own network, the Pi's address there (best a fixed one)." \
"Die Adresse, über die Handys und PCs den Pi erreichen - in QR-Codes und im HTTPS-Zertifikat. Mit Access Point ist das 192.168.4.1; im eigenen Netz die Adresse des Pi dort (am besten eine feste).")" check_host
		;;
	tz)
		ask TIME_ZONE "$(t "Time zone" "Zeitzone")" "$(t "For statistics by hour and weekday, and the kiosk's clock." \
			"Für Statistiken nach Stunde und Wochentag und für die Uhr des Kiosks.")" check_tz
		;;
	backup)
		# Where backups go is decided in the dashboard (Backup): a USB stick the admin sets up
		# there. Here only how often, and how many are kept.
		ask BACKUP_INTERVAL_MINUTES "$(t "Backup interval" "Backup-Intervall")" "$(t "Minutes between two backups to the USB stick set up in the dashboard." \
			"Minuten zwischen zwei Backups auf den USB-Stick, der im Dashboard eingerichtet wird.")" check_number || return 0
		ask BACKUP_DEPTH "$(t "Backups kept" "Aufbewahrte Backups")" "$(t "How many backups to keep before the oldest is deleted." \
			"Wie viele Backups aufbewahrt werden, bevor das älteste gelöscht wird.")" check_number || return 0
		ask AUDIT_RETENTION_DAYS "$(t "Audit log kept (days)" "Protokoll aufbewahren (Tage)")" "$(t "Older audit log entries are deleted automatically." \
			"Ältere Protokolleinträge werden automatisch gelöscht.")" check_number || return 0
		;;
	display)
		local choices=("$(t "1024x600 HDMI panel|the documented touchscreen" "1024x600-HDMI-Panel|der dokumentierte Touchscreen")"
			"$(t "Detected by the display|for screens that report their resolution" "Vom Bildschirm erkannt|für Bildschirme, die ihre Auflösung melden")"
			"$(t "Custom video= mode|e.g. HDMI-A-1:800x480M@60" "Eigener video=-Modus|z.B. HDMI-A-1:800x480M@60")")
		local current=0
		[ "${DISPLAY_MODE}" = auto ] && current=1
		[ "${DISPLAY_MODE}" != panel ] && [ "${DISPLAY_MODE}" != auto ] && current=2
		ui_menu "Display" "$(t \
"Current Raspberry Pi OS ignores hdmi_mode in config.txt - a panel that doesn't report its resolution stays black unless the mode is set here." \
"Aktuelles Raspberry Pi OS ignoriert hdmi_mode in config.txt - ein Panel, das seine Auflösung nicht meldet, bleibt schwarz, wenn der Modus nicht hier gesetzt wird.")" "${current}" "${choices[@]}" || return 0
		case "${MENU}" in
			0) DISPLAY_MODE=panel ;;
			1) DISPLAY_MODE=auto ;;
			2) local mode=${DISPLAY_MODE#video=}
			   if [ "${mode}" = panel ] || [ "${mode}" = auto ]; then mode="HDMI-A-1:1024x600M@60D"; fi
			   DISPLAY_MODE=${mode}
			   ask DISPLAY_MODE "$(t "Custom mode" "Eigener Modus")" "$(t "Kernel video= mode, without the video= prefix." \
				"Kernel-video=-Modus, ohne das Präfix video=.")" check_nonempty && DISPLAY_MODE="video=${DISPLAY_MODE#video=}" ;;
		esac
		;;
	workers)
		ask GUNICORN_WORKERS "$(t "Web workers" "Web-Worker")" "$(t "Processes serving the web app. 2 suits a 1 GB Pi 3, 3-4 a Pi 4 or 5." \
			"Prozesse, die die Web-App ausliefern. 2 passt für einen Pi 3 mit 1 GB, 3-4 für einen Pi 4 oder 5.")" check_number
		;;
	source)
		if ui_confirm "$(t "Install from" "Installieren aus")" "$(t \
			"Install over the internet (the Pi downloads everything)? Choose No to use an offline bundle from scripts/build_offline_bundle.sh instead." \
			"Über das Internet installieren (der Pi lädt alles herunter)? Wähle Nein, um stattdessen ein Offline-Paket aus scripts/build_offline_bundle.sh zu verwenden.")" \
			"$([ "${INSTALL_SOURCE}" = internet ] && echo yes || echo no)"; then
			INSTALL_SOURCE=internet
		else
			INSTALL_SOURCE=offline
			INPUT_CHECK=check_bundle
			ui_input "$(t "Offline bundle" "Offline-Paket")" "$(t \
				"Path on this machine to coffeepy-offline-<date>.tar (about 1.5 GB, copied to the Pi)." \
				"Pfad auf diesem Rechner zu coffeepy-offline-<datum>.tar (etwa 1,5 GB, wird auf den Pi kopiert).")" "${OFFLINE_BUNDLE}" && OFFLINE_BUNDLE=${INPUT}
			INPUT_CHECK=""
		fi
		;;
	esac
}

step_settings() {
	CURRENT_STEP=2
	local selected=0
	while true; do
		local ap_label backup_label source_label install_label
		ap_label=$(t "off - stays on your network" "aus - bleibt in deinem Netz")
		[ "${AP_ENABLE}" = yes ] && ap_label="$(t on an) · ${AP_SSID}"
		backup_label="$(t every alle) ${BACKUP_INTERVAL_MINUTES} min · ${BACKUP_DEPTH} $(t kept aufbewahrt) · $(t log Protokoll) ${AUDIT_RETENTION_DAYS} $(t days Tage)"
		source_label=Internet
		[ "${INSTALL_SOURCE}" = offline ] && source_label="$(t "offline bundle" "Offline-Paket")${OFFLINE_BUNDLE:+ · $(basename "${OFFLINE_BUNDLE}")}"
		install_label=$([ "${EXISTING_INSTALL}" = 1 ] && t "update and apply settings" "aktualisieren und Einstellungen anwenden" || printf '%s %s' "$(t on auf)" "${PI_HOST}")
		local items=(
			"$(t Language Sprache)|$(language_name "${DEVICE_LANGUAGE}")"
			"$(t "Admin account" "Admin-Konto")|${ADMIN_USER} / ${ADMIN_PASSWORD//?/*}"
			"$(t "WiFi access point" "WLAN-Access-Point")|${ap_label}"
			"$(t "Address for phones" "Adresse für Handys")|https://${PUBLIC_HOST}/"
			"$(t "Time zone" "Zeitzone")|${TIME_ZONE}"
			"$(t "Backups & audit log" "Backups & Protokoll")|${backup_label}"
			"Display|$(display_label)"
			"$(t "Web workers" "Web-Worker")|${GUNICORN_WORKERS}"
			"$(t "Install from" "Installieren aus")|${source_label}"
			"---"
			"$(t "Install CoffeePy" "CoffeePy installieren")|${install_label}"
			"$(t Quit Beenden)|$(t "nothing has been changed on the Pi" "auf dem Pi wurde nichts verändert")"
		)
		local keys=(language admin ap address tz backup display workers source "" install quit)
		if ! ui_menu "$(t Settings Einstellungen)" "$(t "Everything that ends up in the Pi's configuration. Pick a line to change it." \
			"Alles, was in die Konfiguration des Pi kommt. Wähle eine Zeile, um sie zu ändern.")" "${selected}" "${items[@]}"; then
			continue
		fi
		selected=${MENU}
		[ "${UNATTENDED}" = 1 ] && selected=10
		case "${keys[$selected]}" in
			install)
				if [ "${INSTALL_SOURCE}" = offline ] && [ ! -f "${OFFLINE_BUNDLE}" ]; then
					ui_message "$(t "Offline bundle missing" "Offline-Paket fehlt")" "$(t \
						"Install from is set to an offline bundle, but no bundle file is chosen." \
						"Installieren aus steht auf Offline-Paket, aber es ist keine Paketdatei gewählt.")"
					[ "${UNATTENDED}" = 1 ] && exit 2
					continue
				fi
				return 0 ;;
			quit) exit 0 ;;
			*) edit_setting "${keys[$selected]}" ;;
		esac
	done
}

# --------------------------------------------------------------------------- install

env_set() { # file key value
	KEY=$2 VAL=$3 awk '
		BEGIN { done = 0 }
		index($0, ENVIRON["KEY"] "=") == 1 { print ENVIRON["KEY"] "=" ENVIRON["VAL"]; done = 1; next }
		{ print }
		END { if (!done) print ENVIRON["KEY"] "=" ENVIRON["VAL"] }
	' "$1" > "$1.new" && mv "$1.new" "$1"
}

build_env() {
	local env="${WORK}/env"
	( umask 077
	  if [ -n "${EXISTING_ENV}" ]; then
		  printf '%s\n' "${EXISTING_ENV}" > "${env}"
	  else
		  cp "${ROOT}/.env.example" "${env}"
		  env_set "${env}" DJANGO_SECRET_KEY "$(rand_token 50)"
		  env_set "${env}" POSTGRES_PASSWORD "$(rand_token 24)"
	  fi )
	env_set "${env}" DJANGO_SUPERUSER_USERNAME "${ADMIN_USER}"
	env_set "${env}" DJANGO_SUPERUSER_PASSWORD "${ADMIN_PASSWORD}"
	env_set "${env}" DJANGO_LANGUAGE_CODE "${DEVICE_LANGUAGE}"
	env_set "${env}" DJANGO_TIME_ZONE "${TIME_ZONE}"
	env_set "${env}" DJANGO_SECURE_COOKIES true
	env_set "${env}" PUBLIC_HOST "${PUBLIC_HOST}"
	# The address this tool reached the Pi on is the one its admin types into a browser too -
	# without it in the certificate and ALLOWED_HOSTS, Django answers that address with a 400.
	local extra="192.168.4.1,coffeepy.local"
	case ",${extra},${PUBLIC_HOST}," in *",${PI_HOST},"*) ;; *) extra+=",${PI_HOST}" ;; esac
	env_set "${env}" TLS_EXTRA_HOSTNAMES "${extra}"
	env_set "${env}" GUNICORN_WORKERS "${GUNICORN_WORKERS}"
	env_set "${env}" BACKUP_INTERVAL_MINUTES "${BACKUP_INTERVAL_MINUTES}"
	env_set "${env}" BACKUP_DEPTH "${BACKUP_DEPTH}"
	env_set "${env}" AUDIT_RETENTION_DAYS "${AUDIT_RETENTION_DAYS}"
	if [ "${AP_ENABLE}" = yes ]; then
		env_set "${env}" AP_SSID "${AP_SSID}"
		env_set "${env}" AP_PASSPHRASE "${AP_PASSPHRASE}"
	else
		# Blank tells the kiosk there is no WiFi of its own to show a QR code for.
		env_set "${env}" AP_SSID ""
		env_set "${env}" AP_PASSPHRASE ""
	fi
	printf '%s' "${env}"
}

# The checkout as git sees it - committed and new files, nothing ignored (.env, .venv,
# data/, this machine's .device/) - or, outside a git checkout, the tree minus the obvious.
pack_source() {
	if git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		(cd "${ROOT}" && git ls-files -z --cached --others --exclude-standard \
			| grep -zv '^\.claude/' | xargs -0 -r ls -d 2>/dev/null | tr '\n' '\0' \
			| tar --null -T - -czf -)
	else
		tar -C "${ROOT}" --exclude=./.git --exclude=./.env --exclude=./.venv --exclude=./data \
			--exclude=./.device --exclude=./.claude -czf - .
	fi
}

step_percent() {
	case "$1" in
		preflight) echo 3 ;; packages) echo 8 ;; app) echo 30 ;; hardware) echo 33 ;;
		kiosk) echo 36 ;; services) echo 40 ;; images) echo 44 ;; ap) echo 88 ;; verify) echo 92 ;;
		*) echo 0 ;;
	esac
}

step_label() {
	case "$1" in
		preflight) t "Checking the Pi" "Pi wird geprüft" ;;
		packages)  t "Installing system packages" "Systempakete werden installiert" ;;
		app)       t "Copying the app" "App wird kopiert" ;;
		hardware)  t "Setting up clock, card reader and display" "Uhr, Kartenleser und Display werden eingerichtet" ;;
		kiosk)     t "Setting up the kiosk screen" "Kiosk-Bildschirm wird eingerichtet" ;;
		services)  t "Installing services" "Dienste werden installiert" ;;
		images)    t "Building the app - the long part" "App wird gebaut - der lange Teil" ;;
		ap)        t "Setting up the WiFi access point" "WLAN-Access-Point wird eingerichtet" ;;
		verify)    t "Starting and checking CoffeePy" "CoffeePy wird gestartet und geprüft" ;;
		*) echo "$1" ;;
	esac
}

step_install() {
	CURRENT_STEP=3
	PROGRESS_TITLE="$(t "Installing on" "Installation auf") ${PI_HOST}"

	ui_progress 1 "$(t "Copying CoffeePy to the Pi" "CoffeePy wird auf den Pi kopiert")" "${ROOT}"
	pack_source | pi 'rm -rf ~/CoffeePy && mkdir -p ~/CoffeePy && tar -xzf - -C ~/CoffeePy' \
		|| fail "$(t "Could not copy the app to the Pi." "Die App konnte nicht auf den Pi kopiert werden.")"

	local offline_flag=""
	if [ "${INSTALL_SOURCE}" = offline ]; then
		ui_progress 2 "$(t "Copying the offline bundle (about 1.5 GB)" "Offline-Paket wird kopiert (etwa 1,5 GB)")" "$(basename "${OFFLINE_BUNDLE}")"
		pi 'rm -rf ~/coffeepy-offline && mkdir -p ~/coffeepy-offline-unpack && tar -xf - -C ~/coffeepy-offline-unpack \
			&& mv ~/coffeepy-offline-unpack/coffeepy-offline ~/coffeepy-offline && rmdir ~/coffeepy-offline-unpack' \
			< "${OFFLINE_BUNDLE}" || fail "$(t "Could not copy the offline bundle." "Das Offline-Paket konnte nicht kopiert werden.")"
		offline_flag="--offline=/home/${PI_USER}/coffeepy-offline"
	fi

	ui_progress 2 "$(t "Writing the configuration" "Konfiguration wird geschrieben")" "/opt/coffeepy/.env"
	local env
	env=$(build_env)
	pi 'umask 077; cat > ~/.coffeepy-env.tmp' < "${env}" \
		|| fail "$(t "Could not upload the configuration." "Die Konfiguration konnte nicht hochgeladen werden.")"
	pi_sudo "sh -c 'install -d /opt/coffeepy && install -m 0600 -o root -g root ~${PI_USER}/.coffeepy-env.tmp /opt/coffeepy/.env && rm -f ~${PI_USER}/.coffeepy-env.tmp'" \
		>/dev/null || fail "$(t "Could not write /opt/coffeepy/.env." "/opt/coffeepy/.env konnte nicht geschrieben werden.")"

	# A re-run repeats every step (--force): new code, a changed WiFi or display mode all
	# live in steps the installer would otherwise skip as done.
	local flags="--yes ${offline_flag}"
	[ "${AP_ENABLE}" = yes ] || flags+=" --skip-ap"
	[ "${EXISTING_INSTALL}" = 1 ] && flags+=" --force"
	local video_env=""
	case "${DISPLAY_MODE}" in
		panel) ;;
		auto) video_env="VIDEO_MODE=" ;;
		*) video_env="VIDEO_MODE=${DISPLAY_MODE}" ;;
	esac
	local home="/home/${PI_USER}"
	pi_sudo "sh -c 'rm -f ${home}/coffeepy-install.rc; cd ${home}/CoffeePy && \
		setsid nohup sh -c \"env ${video_env} ./install/install.sh ${flags} > ${home}/coffeepy-install.log 2>&1; echo \\\$? > ${home}/coffeepy-install.rc\" \
		</dev/null >/dev/null 2>&1 &'" >/dev/null || fail "$(t "Could not start the installer." "Der Installer konnte nicht gestartet werden.")"

	watch_install
}

# Follows ~/coffeepy-install.log until the installer writes its exit code. Survives the
# connection dropping (the access point step can take the Pi off the network you came in on).
watch_install() {
	local percent=3 label detail="" rc="" step lost_since=""
	label=$(t "Starting the installer" "Installer wird gestartet")
	while true; do
		local snapshot
		if snapshot=$(pi 'cat ~/coffeepy-install.rc 2>/dev/null; echo "--"; sed "s/\x1b\[[0-9;]*[A-Za-z]//g" ~/coffeepy-install.log 2>/dev/null | tr "\r" "\n" | grep -v "^\s*$" | tail -n 400' 2>/dev/null); then
			lost_since=""
			rc=$(printf '%s\n' "${snapshot}" | sed -n '1{/^--$/!p}')
			local log=${snapshot#*--$'\n'}
			step=$(printf '%s\n' "${log}" | sed -n 's/^== Step \([a-z]*\).*/\1/p' | tail -1)
			if [ -n "${step}" ]; then
				percent=$(step_percent "${step}")
				label=$(step_label "${step}")
				# The image build is most of the time: creep forward through it.
				if [ "${step}" = images ]; then
					local built
					built=$(printf '%s\n' "${log}" | grep -cE '^(#[0-9]+|Step [0-9]+/)' || true)
					percent=$(( 44 + (built > 400 ? 40 : built / 10) ))
				fi
			fi
			detail=$(printf '%s\n' "${log}" | tail -1)
			printf '%s\n' "${log}" > "${LOCAL_LOG}"
			[ -n "${rc}" ] && break
		else
			# Re-open the shared connection; the Pi may also simply be busy.
			[ -z "${lost_since}" ] && lost_since=${SECONDS}
			detail="$(t "Connection lost" "Verbindung verloren") $((SECONDS - lost_since))s - $(t "the Pi keeps installing, retrying ..." "der Pi installiert weiter, neuer Versuch ...")"
			pi_connect >/dev/null 2>&1 || true
			if [ $((SECONDS - lost_since)) -gt 600 ]; then
				ui_message "$(t "Connection lost" "Verbindung verloren")" "$(t \
"The Pi has not answered for 10 minutes. If you were connected over its WiFi, the access point step has taken that network over - the installation finishes on its own.

Join the WiFi \"${AP_SSID}\" and open https://192.168.4.1/dashboard/ to check, then reboot the Pi." \
"Der Pi antwortet seit 10 Minuten nicht. Wenn du über sein WLAN verbunden warst, hat der Access-Point-Schritt dieses Netz übernommen - die Installation läuft von selbst zu Ende.

Verbinde dich mit dem WLAN \"${AP_SSID}\", öffne zur Kontrolle https://192.168.4.1/dashboard/ und starte den Pi neu.")"
				exit 1
			fi
		fi
		ui_progress "${percent}" "${label}" "${detail}"
		sleep 2
	done
	if [ "${rc}" != 0 ]; then
		cp "${LOCAL_LOG}" "${HOME}/coffeepy-install-${PI_HOST}.log" 2>/dev/null || true
		fail "$(t "The installer stopped (exit code ${rc}). Last lines:" "Der Installer ist abgebrochen (Exit-Code ${rc}). Letzte Zeilen:")

$(tail -n 8 "${LOCAL_LOG}")

$(t "Full log: ${HOME}/coffeepy-install-${PI_HOST}.log - on the Pi: /var/log/coffeepy-install.log. Running this tool again resumes where it stopped." \
"Vollständiges Log: ${HOME}/coffeepy-install-${PI_HOST}.log - auf dem Pi: /var/log/coffeepy-install.log. Ein erneuter Aufruf dieses Tools setzt dort fort, wo es abgebrochen ist.")"
	fi
	# A re-run leaves running containers alone; this recreates those whose image or
	# configuration changed.
	ui_progress 95 "$(t "Applying the configuration" "Konfiguration wird angewendet")" "docker compose up -d"
	pi_sudo "sh -c 'cd /opt/coffeepy && docker compose up -d --remove-orphans'" >/dev/null 2>&1 || true
	ui_progress 100 "$(t Installed Installiert)" ""
}

fail() {
	ui_message "$(t "Something went wrong" "Etwas ist schiefgegangen")" "$1" "$(t "Enter quit" "Enter beenden")"
	exit 1
}

# What the last screen (and the optional file) says - its own function so the user manual's
# screenshot tooling can draw it with example values.
summary_text() { # https-check-result
	local https_ok=$1
	local wifi
	wifi=$(t "(own WiFi off)" "(eigenes WLAN aus)")
	[ "${AP_ENABLE}" = yes ] && wifi="${AP_SSID}  /  $(t password Passwort) ${AP_PASSPHRASE}"
	printf '%s' "CoffeePy $(t on auf) ${PI_HOST} - $(date '+%Y-%m-%d %H:%M')

Dashboard     https://${PUBLIC_HOST}/dashboard/
Admin         ${ADMIN_USER}  /  ${ADMIN_PASSWORD}
$(t "WiFi          " "WLAN          ")${wifi}
$(t "Certificate   " "Zertifikat    ")https://${PUBLIC_HOST}/certificate/  ($(t "install once per phone/PC" "einmal pro Handy/PC installieren"))
$(t "Language      " "Sprache       ")$(language_name "${DEVICE_LANGUAGE}")
$(t "HTTPS check   " "HTTPS-Prüfung ")${https_ok}

$(t "Phones reach the dashboard and their portal through the address above" "Handys erreichen Dashboard und Portal über die Adresse oben")$([ "${AP_ENABLE}" = yes ] && t " after joining the WiFi" ", nachdem sie sich mit dem WLAN verbunden haben").
$(t "Members link their phone from the kiosk's Settings screen." "Mitglieder verknüpfen ihr Handy über den Einstellungen-Bildschirm des Kiosks.")"
}

step_done() {
	CURRENT_STEP=4
	local ca="${WORK}/ca.crt" https_ok
	https_ok=$(t "not checked" "nicht geprüft")
	pi_sudo cat /opt/coffeepy/data/tls/ca.crt > "${ca}" 2>/dev/null || true

	if ui_confirm "$(t Reboot Neustart)" "$(t "The clock, the display mode and the kiosk screen only take effect after a reboot. Reboot the Pi now?" \
		"Uhr, Display-Modus und Kiosk-Bildschirm greifen erst nach einem Neustart. Den Pi jetzt neu starten?")" "${REBOOT}"; then
		pi_sudo "systemctl reboot" >/dev/null 2>&1 || true
		ssh -S "${CONTROL}" -O exit "${PI_USER}@${PI_HOST}" >/dev/null 2>&1 || true
		PROGRESS_TITLE="$(t "Rebooting" "Neustart von") ${PI_HOST}"
		local waited=0
		sleep 10
		while [ "${waited}" -lt 300 ]; do
			ui_progress $(( waited * 100 / 300 )) "$(t "Waiting for the Pi to come back" "Warte, bis der Pi wieder da ist")" "${waited}s"
			if command -v curl >/dev/null && [ -s "${ca}" ] && curl -sf --max-time 5 --cacert "${ca}" \
				--connect-to "coffeepy.local:443:${PI_HOST}:443" https://coffeepy.local/health/ >/dev/null 2>&1; then
				https_ok="$(t "yes" "ja") - https://${PI_HOST}/ $(t "answers with a valid certificate" "antwortet mit gültigem Zertifikat")"
				break
			fi
			sleep 5
			waited=$((waited + 5))
		done
		[ "${waited}" -ge 300 ] && https_ok=$(t "no answer after 5 minutes - expected if the Pi's WiFi replaced the network you reach it on" \
			"keine Antwort nach 5 Minuten - zu erwarten, wenn das WLAN des Pi das Netz ersetzt hat, über das du ihn erreichst")
	fi

	local summary
	summary=$(summary_text "${https_ok}")
	if ui_confirm "$(t "Save the passwords?" "Passwörter speichern?")" "${summary}

$(t "Save this summary - passwords included - to ${HOME}/coffeepy-${PI_HOST}.txt (readable only by you)?" \
"Diese Zusammenfassung - mit Passwörtern - unter ${HOME}/coffeepy-${PI_HOST}.txt speichern (nur für dich lesbar)?")" "${SAVE_SUMMARY}"; then
		( umask 077; printf '%s\n' "${summary}" > "${HOME}/coffeepy-${PI_HOST}.txt" )
		summary+="

$(t "Saved to" "Gespeichert unter") ${HOME}/coffeepy-${PI_HOST}.txt"
	fi
	ui_message "$(t "CoffeePy is ready" "CoffeePy ist bereit")" "${summary}" "$(t "Enter quit" "Enter beenden")"
	return 0
}

# --------------------------------------------------------------------------- main

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

load_answers() {
	local line key value
	while IFS= read -r line || [ -n "${line}" ]; do
		[[ "${line}" =~ ^[[:space:]]*(#|$) ]] && continue
		key=${line%%=*} value=${line#*=}
		# Only known keys, and never sourced: an answers file is data, not a script.
		[[ " ${ANSWER_KEYS//$'\n'/ } " == *" ${key} "* ]] || { echo "Unknown key in ${ANSWERS}: ${key}" >&2; exit 2; }
		printf -v "${key}" '%s' "${value}"
	done < "$1"
}

main() {
	while [ $# -gt 0 ]; do
		case "$1" in
			--answers) ANSWERS=${2:?--answers needs a file}; shift ;;
			--answers=*) ANSWERS=${1#--answers=} ;;
			--unattended) UNATTENDED=1 ;;
			-h|--help) usage 0 ;;
			*) echo "Unknown option: $1" >&2; usage 1 ;;
		esac
		shift
	done
	[ -n "${ANSWERS}" ] && load_answers "${ANSWERS}"
	# Given (answers or environment) beats what an earlier installation's .env says -
	# step_language fills DEVICE_LANGUAGE in later, so remember whether it was asked for.
	DEVICE_LANGUAGE_GIVEN=${DEVICE_LANGUAGE}
	local missing="" tool
	for tool in ssh tar awk fold; do command -v "${tool}" >/dev/null || missing+=" ${tool}"; done
	[ -n "${missing}" ] && { echo "Missing on this machine:${missing}" >&2; exit 1; }
	[ "${BASH_VERSINFO[0]}" -ge 4 ] || { echo "Needs bash 4 or newer." >&2; exit 1; }

	tui_init
	if [ "${TUI_ACTIVE}" = 1 ] && { [ "${COLS}" -lt 80 ] || [ "${ROWS}" -lt 24 ]; }; then
		tui_end; echo "Needs a terminal of at least 80x24 (this one is ${COLS}x${ROWS})." >&2; exit 1
	fi
	step_language
	step_connect
	inspect_pi
	load_existing
	defaults
	step_settings
	step_install
	step_done
}

# Sourced (the user manual's screenshot tooling draws single screens with these functions),
# it only defines them; run, it provisions.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	main "$@"
fi
