# shellcheck shell=bash
# Sourced by install.sh and update_watcher.sh: tells the kiosk that the device is being
# worked on, so it shows "back in a moment" instead of taking orders - or failing to, while
# the app restarts underneath it.
#
# The status is a small JSON file in data/maintenance/, which Caddy serves itself at
# /maintenance/status.json (dashboard/tls.py writes that route): it has to answer while the
# app is down, which is exactly when it matters. static/kiosk/maintenance.js reads it. The
# file's timestamp lets the kiosk ignore one left behind by a process that died.

MAINTENANCE_DIR="${MAINTENANCE_DIR:-/opt/coffeepy/data/maintenance}"
MAINTENANCE_KIND=""

# maintenance_begin install|update
maintenance_begin() {
	MAINTENANCE_KIND=$1
	maintenance_step starting 0
}

# maintenance_step <step key> <percent> - the step keys are translated by the kiosk
# (kiosk.views.MAINTENANCE_STEPS); an unknown one shows without a label.
maintenance_step() {
	[ -n "${MAINTENANCE_KIND}" ] || return 0
	mkdir -p "${MAINTENANCE_DIR}" 2>/dev/null || return 0
	local tmp="${MAINTENANCE_DIR}/.status.json.tmp"
	printf '{"kind": "%s", "step": "%s", "percent": %d, "updated": %d}\n' \
		"${MAINTENANCE_KIND}" "$1" "$2" "$(date +%s)" > "${tmp}" 2>/dev/null || return 0
	chmod 0644 "${tmp}"
	mv -f "${tmp}" "${MAINTENANCE_DIR}/status.json"
}

maintenance_end() {
	[ -n "${MAINTENANCE_KIND}" ] || return 0
	rm -f "${MAINTENANCE_DIR}/status.json" 2>/dev/null || true
	MAINTENANCE_KIND=""
}
