#!/bin/bash
# Retakes the manual's screenshots - all of them, or some areas - into docs/pictures/manual/.
#
#   capture.sh                                  everything, German and English
#   capture.sh --only kiosk,dashboard,portal,provision    any of them, comma-separated
#
# The kiosk is captured on the test Pi itself (pi-device skill: the kiosk browser's debug
# port, demo cards). Dashboard and portal are captured from this machine, in a throwaway
# headless Chromium profile pointed at the Pi - nothing on the touchscreen changes for them.
# Demo data is added first and - whatever happens - removed again, and the debug port is
# turned off. Needs a Python with Pillow in $MANUAL_PYTHON (see capture_tui.sh) and
# chromium-browser (or $CHROMIUM) here.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "${DIR}" rev-parse --show-toplevel)"
PI="${ROOT}/.claude/skills/pi-device/scripts"
OUT="${ROOT}/docs/pictures/manual"
PY=${MANUAL_PYTHON:-python3}
CHROMIUM=${CHROMIUM:-chromium-browser}
ONLY=${2:-kiosk,dashboard,portal,provision}
[ "${1:-}" = --only ] || ONLY=kiosk,dashboard,portal,provision
wants() { [[ ",${ONLY}," == *",$1,"* ]]; }
. "${ROOT}/.device/pi.env"

"${PY}" -c "import PIL" 2>/dev/null || { echo "Needs Pillow - set MANUAL_PYTHON, see capture_tui.sh" >&2; exit 1; }

WORK="$(mktemp -d)"
BROWSER_PID=""
cleanup() {
	[ -n "${BROWSER_PID}" ] && kill "${BROWSER_PID}" 2>/dev/null
	if wants kiosk || wants dashboard || wants portal; then
		"${PI}/django.sh" "${DIR}/demo_data.py" cleanup || true
		"${PI}/pi.sh" "rm -rf /tmp/manual /tmp/capture_web.py /tmp/demo_data.py" || true
	fi
	wants kiosk && { "${PI}/kiosk-browser.sh" off || true; }
	rm -rf "${WORK}"
}
trap cleanup EXIT

if wants kiosk || wants dashboard || wants portal; then
	scp -q "${PI}/cdp.py" "${DIR}/demo_data.py" "${DIR}/capture_web.py" "${PI_USER}@${PI_HOST}:/tmp/"
	"${PI}/django.sh" "${DIR}/demo_data.py" seed
fi

if wants kiosk; then
	"${PI}/kiosk-browser.sh" on
	"${PI}/kiosk-browser.sh" errors >/dev/null   # waits for the kiosk page to load
	"${PI}/pi.sh" "rm -rf /tmp/manual; python3 -I /tmp/capture_web.py de en --only kiosk"
	mkdir -p "${OUT}"
	scp -q -r "${PI_USER}@${PI_HOST}:/tmp/manual/." "${OUT}/"
fi

areas=""
wants dashboard && areas+="dashboard,"
wants portal && areas+="portal,"
if [ -n "${areas}" ]; then
	for lang in de en; do
		# A profile of its own: this machine's browser profile and certificate store stay as
		# they are, so the device CA is not trusted here - hence --ignore-certificate-errors.
		# One browser per language: what the browser draws itself ("Choose File") follows its
		# own UI language - which headless Chromium takes from LANGUAGE, not from --lang.
		local_lang=$([ "${lang}" = de ] && echo de_DE.UTF-8 || echo en_US.UTF-8)
		LANGUAGE="${lang}" LANG="${local_lang}" "${CHROMIUM}" --headless=new --remote-debugging-port=9333 \
			--user-data-dir="${WORK}/profile-${lang}" --ignore-certificate-errors --no-first-run --hide-scrollbars \
			about:blank >/dev/null 2>&1 &
		BROWSER_PID=$!
		for _ in $(seq 40); do curl -s localhost:9333/json >/dev/null 2>&1 && break; sleep 0.5; done
		CDP_PORT=9333 CDP_PATH="${PI}" CAPTURE_BASE="https://${PI_HOST}" CAPTURE_OUT="${OUT}" \
			CAPTURE_SSH="ssh -o BatchMode=yes ${PI_USER}@${PI_HOST}" \
			python3 -I "${DIR}/capture_web.py" "${lang}" --only "${areas%,}"
		kill "${BROWSER_PID}" 2>/dev/null; wait "${BROWSER_PID}" 2>/dev/null || true
		BROWSER_PID=""
	done
fi

if wants provision; then
	"${DIR}/capture_tui.sh" "${OUT}" de en
fi

# Flat UI colours survive a 256-colour palette untouched, and the files shrink to a third.
"${PY}" - "${OUT}" <<'PYEOF'
import pathlib, sys
from PIL import Image
for path in pathlib.Path(sys.argv[1]).rglob("*.png"):
    image = Image.open(path)
    if image.mode != "P":
        image.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)
PYEOF
du -sh "${OUT}"
