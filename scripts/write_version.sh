#!/bin/bash
# Writes coffeepy-version.json into the checkout: which commit this code is, for the
# dashboard's version list (dashboard/versions.py) and the update bundles - the image and
# the device have no .git to ask. Everything that builds or ships the tree calls it first:
# scripts/build_update_bundle.sh, scripts/provision.sh, install/install.sh (when run from a
# checkout) and the pi-device skill's sync.sh. Outside a git checkout it keeps whatever file
# is already there.
#
#   scripts/write_version.sh      prints the version it wrote
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/coffeepy-version.json"

if ! command -v git >/dev/null || ! git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	[ -f "${OUT}" ] && sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "${OUT}"
	exit 0
fi
# Without a tag, describe falls back to the abbreviated commit - unique either way; -dirty
# marks a tree with uncommitted changes, which no commit hash alone would reveal.
version="$(git -C "${ROOT}" describe --tags --always --dirty)"
commit="$(git -C "${ROOT}" rev-parse HEAD)"
committed="$(git -C "${ROOT}" show -s --format=%cI HEAD)"
branch="$(git -C "${ROOT}" rev-parse --abbrev-ref HEAD | tr -d '"\\')"
printf '{"version": "%s", "commit": "%s", "committed": "%s", "branch": "%s", "written": "%s"}\n' \
	"${version}" "${commit}" "${committed}" "${branch}" "$(date -Iseconds)" > "${OUT}"
echo "${version}"
