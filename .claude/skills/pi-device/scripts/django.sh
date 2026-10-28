#!/bin/bash
# Runs a local Python file inside the Pi's web container, through `manage.py shell` (so
# Django is set up and talks to the device's real Postgres).
#
#   django.sh perf_data.py seed
#   django.sh page_timings.py
#
# Arguments reach the script as the SCRIPT_ARGS environment variable (manage.py shell
# reads the code from stdin and has no argv of its own).
set -euo pipefail

FILE="${1:?usage: django.sh FILE.py [ARGS...]}"
shift
DIR="$(dirname "$0")"
[ -f "$FILE" ] || FILE="$DIR/$FILE"
"$DIR/pi.sh" "cd /opt/coffeepy && sudo docker compose exec -T -e SCRIPT_ARGS='$*' web python manage.py shell" \
	< "$FILE" 2>&1 | grep -v "objects imported automatically"
