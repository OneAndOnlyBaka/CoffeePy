#!/bin/bash
# Renders every 3D shot that isn't complete yet - safe to start again after an interruption (frames already on
# disk are kept). Delete render/<shot>/ to redo one. Needs get_blender.sh, the recordings (scene_kiosk.py) and
# model.blend + scene.blend (import.py, build_scene.py - see SKILL.md).
set -e
REPO=$(cd "$(dirname "$(readlink -f "$0")")/../../../.." && pwd)
WORK=${VIDEO_WORK:-$REPO/.render/video/work}
HERE=$(dirname "$(readlink -f "$0")")
B=$(ls -d "$WORK"/blender/blender-*/blender | head -1)
# The display shows these kiosk recordings as image sequences (Blender reads PNGs reliably, FFV1 not necessarily)
for clip in kiosk_wait kiosk_order kiosk_ranking; do
  if [ ! -d "$WORK/seq/$clip" ]; then
    mkdir -p "$WORK/seq/$clip"
    ffmpeg -loglevel error -i "$WORK/clips/$clip.mkv" -start_number 1 "$WORK/seq/$clip/%04d.png"
  fi
done
declare -A LEN=([hero]=240 [explode]=270 [order]=330 [ranking]=390 [end]=150)
for shot in ${@:-hero explode order ranking end}; do
  n=$(ls "$WORK/render/$shot" 2>/dev/null | wc -l)
  if [ "$n" -ge "${LEN[$shot]}" ]; then echo "$shot done"; continue; fi
  start=$(date +%s)
  "$B" -b "$WORK/3d/scene.blend" -P "$HERE/shots.py" -- $shot render > "$WORK/render/$shot.log" 2>&1 ||
    { echo "$shot failed - $WORK/render/$shot.log"; exit 1; }
  echo "$shot: $(ls "$WORK/render/$shot" | wc -l) frames in $(( $(date +%s) - start ))s"
done
