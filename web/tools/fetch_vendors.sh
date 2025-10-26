#!/usr/bin/env bash
# Simple helper to download required vendor files for offline use.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# repository root is one level above the web/ folder which contains this script
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$ROOT_DIR/files/vendor"

mkdir -p "$VENDOR_DIR/flatpickr"
mkdir -p "$VENDOR_DIR/chartjs"


DL_CMD=""
if command -v curl >/dev/null 2>&1; then
	DL_CMD="curl -fsSL -o"
elif command -v wget >/dev/null 2>&1; then
	# wget -q -O <file> <url>
	DL_CMD="wget -q -O"
else
	echo "Error: neither 'curl' nor 'wget' is installed. Please install one and re-run this script." >&2
	exit 2
fi

echo "Downloading flatpickr..."
${DL_CMD} "$VENDOR_DIR/flatpickr/flatpickr.min.css" https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css
${DL_CMD} "$VENDOR_DIR/flatpickr/flatpickr.min.js" https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.js

echo "Downloading Chart.js..."
${DL_CMD} "$VENDOR_DIR/chartjs/chart.umd.min.js" https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js

echo "Downloaded vendor files into $VENDOR_DIR"

echo "Files:"
ls -R "$VENDOR_DIR"

echo "Done. Open web/files/index.html in a browser (file://) or serve via a local static server to verify offline functionality."
