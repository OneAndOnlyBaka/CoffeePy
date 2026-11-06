#!/bin/bash
# The CC0 track the first video used, with its licence note. Re-check the licence on the track page before
# publishing anything new - it is what the page says on the day you look.
set -e
REPO=$(cd "$(dirname "$(readlink -f "$0")")/../../../.." && pwd)
WORK=${VIDEO_WORK:-$REPO/.render/video/work}
mkdir -p "$WORK/music" && cd "$WORK/music"
[ -f roof-tops.mp3 ] || curl -sSfL -o roof-tops.mp3 \
  https://files.freemusicarchive.org/storage-freemusicarchive-org/tracks/2hFTiuqYI7onbLzpi9ldoBP8AYeuStsCUxTqm6vd.mp3
cat > MUSIC.txt <<'TXT'
Music: "Roof Tops" by HoliznaCC0 (album "Summer Air (Lo-fi)")
Source:  https://freemusicarchive.org/music/holiznacc0/summer-air-lo-fi/roof-tops/
License: CC0 1.0 Universal (public domain dedication) - https://creativecommons.org/publicdomain/zero/1.0/
         as stated on the track page, checked 2026-11-02. No attribution required.
TXT
echo "music ready"
