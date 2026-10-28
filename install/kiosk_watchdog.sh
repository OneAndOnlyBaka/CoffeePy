#!/bin/sh
# What people at the legacy device did by pulling the plug - some 35 times in its last year,
# always in office hours, often twice a day - done before anyone has to: every minute
# (coffeepy-watchdog.timer) this checks that the app answers and that the kiosk's browser is
# still polling it, and restarts what doesn't, a step at a time:
#
#   app not answering for STACK_CHECKS minutes in a row -> restart the stack
#   kiosk not polling (stale data/storage/kiosk-heartbeat, see kiosk/views.py)
#     -> restart the kiosk session; still not polling after that -> restart the stack
#
# Never while the device is being worked on (data/maintenance/status.json: an update, a
# restore, an import, the installer), never in the first minutes after a boot or after its
# own last step, and at most MAX_ACTIONS steps an hour - a device that a restart doesn't
# help must not be kept restarting. Every step goes into the audit log.
set -u

DIR="${COFFEEPY_DIR:-/opt/coffeepy}"
STORAGE="${COFFEEPY_STORAGE_DIR:-$DIR/data/storage}"
MAINTENANCE="${COFFEEPY_MAINTENANCE_FILE:-$DIR/data/maintenance/status.json}"
STATE="${WATCHDOG_STATE_DIR:-/run/coffeepy-watchdog}"  # tmpfs: a reboot starts afresh
HEALTH_URL="${WATCHDOG_HEALTH_URL:-https://localhost:8443/health/}"
CA="${WATCHDOG_CA:-$DIR/data/tls/ca.crt}"
GRACE="${WATCHDOG_GRACE_SECONDS:-300}"        # after boot and after each step: time to come up
STACK_CHECKS="${WATCHDOG_STACK_CHECKS:-3}"     # minutes without an answer before the stack goes
STALE="${WATCHDOG_HEARTBEAT_SECONDS:-180}"     # the kiosk polls twice a second, beats every 30s
MAX_ACTIONS="${WATCHDOG_MAX_ACTIONS:-3}"       # per hour
UPTIME="${WATCHDOG_UPTIME:-$(cut -d. -f1 /proc/uptime)}"
NIGHT_CHECK="${WATCHDOG_NIGHT_CHECK:-python3 $DIR/install/kiosk_night.py}"  # 0 at night
NOW="$(date +%s)"

mkdir -p "$STATE"

event() { # action details
    printf '{"time": %s, "action": "%s", "actor": "watchdog", "target": "%s", "details": "%s", "outcome": "failure"}\n' \
        "$NOW" "$1" "$2" "$3" >> "$STORAGE/events.jsonl"
    logger -t coffeepy-watchdog "$1: $3" 2>/dev/null || true
}

act() { # action target details - then the command line to run
    action=$1 target=$2 details=$3
    shift 3
    recent=0
    if [ -f "$STATE/actions" ]; then
        recent=$(awk -v since=$((NOW - 3600)) '$1 >= since' "$STATE/actions" | wc -l)
    fi
    if [ "$recent" -ge "$MAX_ACTIONS" ]; then
        # Once per hour of giving up, not every minute.
        if [ ! -f "$STATE/gave-up" ] || [ $((NOW - $(cat "$STATE/gave-up"))) -ge 3600 ]; then
            event watchdog.gave_up "$target" "$details - but $MAX_ACTIONS restarts within the hour did not help; leaving it"
            echo "$NOW" > "$STATE/gave-up"
        fi
        return 0
    fi
    event "$action" "$target" "$details"
    echo "$NOW" >> "$STATE/actions"
    echo "$NOW" > "$STATE/last-action"
    "$@" || event watchdog.failed "$target" "$* failed"
}

[ "$UPTIME" -lt "$GRACE" ] && exit 0
if [ -f "$STATE/last-action" ] && [ $((NOW - $(cat "$STATE/last-action"))) -lt "$GRACE" ]; then
    exit 0
fi
# A status left behind by a process that died is ignored after two hours - as the kiosk does.
if [ -f "$MAINTENANCE" ] && [ $((NOW - $(stat -c %Y "$MAINTENANCE"))) -lt 7200 ]; then
    rm -f "$STATE/failures"
    exit 0
fi

if ! curl -sf --max-time 15 --cacert "$CA" "$HEALTH_URL" >/dev/null 2>&1; then
    failures=$(( $(cat "$STATE/failures" 2>/dev/null || echo 0) + 1 ))
    echo "$failures" > "$STATE/failures"
    if [ "$failures" -ge "$STACK_CHECKS" ]; then
        rm -f "$STATE/failures"
        act watchdog.stack_restart coffeepy.service "the app did not answer for $failures minutes" \
            systemctl restart coffeepy.service
    fi
    exit 0
fi
rm -f "$STATE/failures"

# The kiosk's night (dashboard → System): no browser on purpose, nothing to repair.
if $NIGHT_CHECK >/dev/null 2>&1; then
    rm -f "$STATE/kiosk-restarted"
    exit 0
fi
# No heartbeat file at all: no kiosk browser has ever polled this stack - nothing to judge.
[ -f "$STORAGE/kiosk-heartbeat" ] || exit 0
age=$((NOW - $(stat -c %Y "$STORAGE/kiosk-heartbeat")))
if [ "$age" -lt "$STALE" ]; then
    rm -f "$STATE/kiosk-restarted"
    exit 0
fi
# A browser younger than that is still starting - in the morning after the night, say, when
# the last heartbeat is the evening's. The session's own loop restarts one that never loads.
browser=$(pgrep -o -f "^/usr/lib/chromium/chromium" 2>/dev/null)
if [ -n "$browser" ] && [ "$(ps -o etimes= -p "$browser" | tr -d ' ')" -lt "$STALE" ] 2>/dev/null; then
    exit 0
fi
if [ -f "$STATE/kiosk-restarted" ]; then
    # The session was restarted and the browser still doesn't poll: the app answers its
    # health check, but maybe not the kiosk's own pages.
    rm -f "$STATE/kiosk-restarted"
    act watchdog.stack_restart coffeepy.service "the kiosk still did not poll after restarting it" \
        systemctl restart coffeepy.service
else
    echo "$NOW" > "$STATE/kiosk-restarted"
    act watchdog.kiosk_restart getty@tty1.service "the kiosk had not polled for ${age}s" \
        systemctl restart getty@tty1.service
fi
exit 0
