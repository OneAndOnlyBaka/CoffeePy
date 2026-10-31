#!/bin/bash
# Screenshots of scripts/provision.sh for the manual, in one or both languages.
#
#   capture_tui.sh OUT_DIR de en
#
# The screens up to the settings menu come from a real run against the test Pi (pi-device
# skill, .device/pi.env) that is quit from the menu - nothing gets installed. Progress and
# the final summary are drawn by sourcing provision.sh with example values, so no real
# install has to run and no real password ends up in a picture. Needs tmux, and a Python
# with Pillow in $MANUAL_PYTHON (python3 -m venv /tmp/manual && /tmp/manual/bin/pip install pillow).
set -euo pipefail

OUT=${1:?usage: capture_tui.sh OUT_DIR de|en ...}
shift
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "${DIR}" rev-parse --show-toplevel)"
PY=${MANUAL_PYTHON:-python3}
. "${ROOT}/.device/pi.env"
SESSION=manual-tui
WORK="$(mktemp -d)"
trap 'tmux kill-session -t ${SESSION} 2>/dev/null; rm -rf "${WORK}"' EXIT

start() { # command...
	tmux kill-session -t "${SESSION}" 2>/dev/null || true
	tmux new-session -d -s "${SESSION}" -x 100 -y 30 "cd ${ROOT} && COLORTERM=truecolor LANG=C.UTF-8 $*; sleep 600"
	sleep 1.5
}
keys() { tmux send-keys -t "${SESSION}" "$@"; sleep 0.6; }
text() { tmux send-keys -t "${SESSION}" -l "$1"; sleep 0.4; }
until_screen() { # pattern
	for _ in $(seq 60); do tmux capture-pane -t "${SESSION}" -p | grep -q "$1" && return 0; sleep 0.5; done
	echo "screen never showed: $1" >&2; return 1
}
shot() { # lang name
	mkdir -p "${OUT}/$1"
	tmux capture-pane -t "${SESSION}" -e -p > "${WORK}/pane.txt"
	"${PY}" "${DIR}/ansi2png.py" "${WORK}/pane.txt" "${OUT}/$1/$2.png"
	echo "  $1/$2.png"
}

for lang in "$@"; do
	# The first screen picks this tool's own language - shown once, it is bilingual.
	start ./scripts/provision.sh
	until_screen "Deutsch"
	[ "${lang}" = de ] && shot "${lang}" provision-language
	if [ "${lang}" = de ]; then keys Up; fi
	keys Enter
	shot "${lang}" provision-welcome
	keys Enter
	text "${PI_HOST}"; shot "${lang}" provision-host; keys Enter
	text "${PI_USER}"; keys Enter
	text "${PI_PASSWORD}"; shot "${lang}" provision-password; keys Enter
	until_screen "Raspberry Pi 3\|Raspberry Pi 4\|Raspberry Pi 5\|Model"
	sleep 1; shot "${lang}" provision-check
	# With a warning (connected over the Pi's own WiFi, say) the check asks to continue, "No"
	# preselected - Enter alone would quit the tool, and every later shot came out blank.
	tmux capture-pane -t "${SESSION}" -p | grep -q "Continue anyway\|Trotzdem fortfahren" && keys Left
	keys Enter
	sleep 2; shot "${lang}" provision-existing; keys Enter
	sleep 1; shot "${lang}" provision-settings
	keys Down; keys Down; keys Enter          # WiFi access point
	shot "${lang}" provision-wifi
	keys Enter; keys Enter                     # yes, keep the name
	keys C-u; text "short"; keys Enter         # a passphrase that is too short
	shot "${lang}" provision-validation
	keys Escape
	keys Down; keys Down; keys Down; keys Down; keys Enter   # Updates (the signature check)
	shot "${lang}" provision-updates
	keys Escape
	for _ in 1 2 3 4 5 6 7; do keys Up; done; keys Enter     # Quit - wraps round to the last line
	sleep 1

	# The update package builder shares the provisioning tool's screens.
	# Only looked at, never answered: Enter on its first screen starts a build.
	start "UI_LANG=${lang} COFFEEPY_SIGNING_KEY=/home/you/.config/coffeepy/update-signing.key OUTPUT_DIR=/home/you/CoffeePy ./scripts/update_builder.sh"
	until_screen "linux/"
	sleep 1; shot "${lang}" update-builder
	tmux kill-session -t "${SESSION}"

	# Progress and the final summary, drawn with example values.
	common="source scripts/provision.sh; UI_LANG=${lang}; DEVICE_LANGUAGE=${lang}; tui_init; PI_HOST=192.168.10.13; PI_USER=pi;
		PUBLIC_HOST=192.168.4.1; ADMIN_USER=admin; ADMIN_PASSWORD=Example-Pass1; AP_ENABLE=yes; AP_SSID=CoffeePy; AP_PASSPHRASE=Example-WiFi1;
		BACKUP_PASSPHRASE=Example-Backup-Passphrase1; SSH_AFTER_INSTALL=off"
	start bash -c "'${common}; CURRENT_STEP=3; PROGRESS_TITLE=\"\$(t \"Installing on\" \"Installation auf\") 192.168.10.13\";
		ui_progress 61 \"\$(step_label images)\" \"Step 9/15 : RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt\"; sleep 600'"
	until_screen "61%"
	shot "${lang}" provision-progress
	start bash -c "'${common}; CURRENT_STEP=4; s=\$(summary_text \"\$(t \"yes - https://192.168.10.13/ answers with a valid certificate\" \"ja - https://192.168.10.13/ antwortet mit gültigem Zertifikat\")\");
		ui_message \"\$(t \"CoffeePy is ready\" \"CoffeePy ist bereit\")\" \"\$s\" \"\$(t \"Enter quit\" \"Enter beenden\")\"'"
	until_screen "CoffeePy"
	sleep 1; shot "${lang}" provision-done
done
