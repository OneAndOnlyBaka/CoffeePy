#!/bin/sh
# Run podman, whether or not we're inside a sandbox.
#
# VS Code installed as a Flatpak runs its tasks inside the sandbox, where $PATH is
# /app/bin:/usr/bin and the host's podman simply isn't there ("podman: command not
# found"). flatpak-spawn --host punches out to the real host to run it. Outside a
# sandbox the first branch wins and this is a no-op wrapper.
set -eu

if command -v podman >/dev/null 2>&1; then
    exec podman "$@"
fi

if command -v flatpak-spawn >/dev/null 2>&1; then
    exec flatpak-spawn --host podman "$@"
fi

echo "podman not found, and no flatpak-spawn available to reach the host." >&2
exit 127
