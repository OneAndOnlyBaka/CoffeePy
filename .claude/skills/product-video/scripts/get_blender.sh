#!/bin/bash
# Portable Blender 4.5 LTS into the work dir, checksum-verified - nothing installed on the system. The scripts are
# written against 4.5's API (EEVEE Next, OBJ importer); a newer major may need changes.
set -e
REPO=$(cd "$(dirname "$(readlink -f "$0")")/../../../.." && pwd)
WORK=${VIDEO_WORK:-$REPO/.render/video/work}
VER=${BLENDER_VERSION:-4.5.15}
[ -x "$WORK/blender/blender-$VER-linux-x64/blender" ] && { echo "blender $VER already there"; exit 0; }
mkdir -p "$WORK/blender" && cd "$WORK/blender"
curl -sSfLO "https://download.blender.org/release/Blender${VER%.*}/blender-$VER-linux-x64.tar.xz"
curl -sSfL "https://download.blender.org/release/Blender${VER%.*}/blender-$VER.sha256" | grep linux-x64 | sha256sum -c
tar xf "blender-$VER-linux-x64.tar.xz" && rm "blender-$VER-linux-x64.tar.xz"
echo "blender $VER ready"
