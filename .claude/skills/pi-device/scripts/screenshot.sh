#!/bin/bash
# Grabs what the touchscreen shows right now and saves it locally as a PNG.
#
#   screenshot.sh /path/to/out.png
#
# Needs x11-apps (xwd) and imagemagick on the Pi - not part of the install, so this adds
# them on first use. Prints the mean brightness too: 1.0 is a blank white window, 0 black.
set -euo pipefail

OUT="${1:?usage: screenshot.sh OUT.png}"
DIR="$(dirname "$0")"
"$DIR/pi.sh" 'command -v xwd >/dev/null && command -v convert >/dev/null || sudo apt-get install -y --no-install-recommends x11-apps imagemagick >/dev/null 2>&1
DISPLAY=:0 xwd -root -silent | convert xwd:- png:/tmp/kiosk-screen.png
echo "brightness $(convert /tmp/kiosk-screen.png -format "%[fx:mean]" info:)"'
. "$(git -C "$DIR" rev-parse --show-toplevel)/.device/pi.env"
scp -q "$PI_USER@$PI_HOST:/tmp/kiosk-screen.png" "$OUT"
echo "saved $OUT"
