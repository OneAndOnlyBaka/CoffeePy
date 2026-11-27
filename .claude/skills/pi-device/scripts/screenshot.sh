#!/bin/bash
# What the touchscreen shows, as a PNG here. Prints the mean brightness: 1.0 blank white, 0 black,
# ~0.81 the kiosk.
#
#   screenshot.sh OUT.png
#
# cog draws straight on KMS, with no X to ask: ffmpeg's kmsgrab reads the scanout buffer, which
# is in the VC4's tiled layout, and kms_untile.py puts it back in rows (the bottom right 1024x8
# pixels are missing - the grab stops at 600 rows, the tiles go on to 608). A device that still
# runs the Chromium session is read through X (xwd).
set -euo pipefail

OUT="${1:?usage: screenshot.sh OUT.png}"
DIR="$(dirname "$0")"
. "$(git -C "$DIR" rev-parse --show-toplevel)/.device/pi.env"
if "$DIR/pi.sh" 'pgrep -x cog >/dev/null'; then
	scp -q "$DIR/kms_untile.py" "$PI_USER@$PI_HOST:/tmp/kms_untile.py"
	"$DIR/pi.sh" 'command -v ffmpeg >/dev/null || sudo apt-get install -y --no-install-recommends ffmpeg >/dev/null 2>&1
for i in 1 2 3; do
	sudo ffmpeg -loglevel error -y -device /dev/dri/card0 -f kmsgrab -i - -vf hwdownload,format=bgr0 \
		-frames:v 1 -f rawvideo /tmp/kiosk-screen.raw && break
	sleep 1
done
python3 -I /tmp/kms_untile.py /tmp/kiosk-screen.raw 1024 600 /tmp/kiosk-screen.png'
else
	"$DIR/pi.sh" 'command -v xwd >/dev/null && command -v convert >/dev/null || sudo apt-get install -y --no-install-recommends x11-apps imagemagick >/dev/null 2>&1
DISPLAY=:0 xwd -root -silent | convert xwd:- png:/tmp/kiosk-screen.png
echo "brightness $(convert /tmp/kiosk-screen.png -format "%[fx:mean]" info:)"'
fi
scp -q "$PI_USER@$PI_HOST:/tmp/kiosk-screen.png" "$OUT"
echo "saved $OUT"
