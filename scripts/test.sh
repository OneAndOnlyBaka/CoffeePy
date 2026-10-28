#!/bin/bash
# Runs the test suite in containers (docker-compose.test.yml): real PostgreSQL, the app's
# own command line tools, coverage report at the end.
#
#   scripts/test.sh                                 all tests
#   scripts/test.sh dashboard.tests.test_backup     some
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
	compose=(docker compose)
else
	compose=(podman-compose)
fi
export TEST_LABELS="$*"
status=0
"${compose[@]}" -f docker-compose.test.yml run --rm -e TEST_LABELS="$*" tests || status=$?
"${compose[@]}" -f docker-compose.test.yml down >/dev/null 2>&1 || true
exit "$status"
