#!/bin/sh
set -e

PCSCD_ARGS="--foreground --disable-polkit"
if [ "${PCSCD_DEBUG:-false}" = "true" ]; then
    PCSCD_ARGS="$PCSCD_ARGS --debug"
fi

# pcscd's stdout/stderr is relayed through this FIFO so the watcher below can inspect
# it line by line while it still reaches this container's own stdout (`docker/podman
# logs`) - see that watcher for why.
PCSCD_LOG_FIFO="$(mktemp -u)"
mkfifo "$PCSCD_LOG_FIFO"
RESTART_MARKER="$(mktemp -u)"

pcscd $PCSCD_ARGS > "$PCSCD_LOG_FIFO" 2>&1 &
PCSCD_PID=$!

term_handler() {
    # Stop the watchers first - otherwise they'd see pcscd exit from our own kill below
    # and mistake a normal shutdown for the failure cases they exist to catch.
    kill -TERM "$LOGWATCH_PID" "$WATCHER_PID" 2>/dev/null || true
    kill -TERM "$APP_PID" 2>/dev/null || true
    kill -TERM "$PCSCD_PID" 2>/dev/null || true
    wait "$PCSCD_PID" 2>/dev/null || true
    rm -f "$PCSCD_LOG_FIFO" "$RESTART_MARKER"
    exit 0
}
trap term_handler TERM INT

# Relays pcscd's log to our own stdout unchanged, while also watching for a failure
# mode this project actually hit in the field: unplugging the ACR122U doesn't reliably
# make pcscd exit - its CCID driver can instead get stuck endlessly retrying USB I/O
# against the now-gone device handle (libccid logs "LIBUSB_ERROR_NO_DEVICE" on every
# attempt), with the reader still listed as present to PC/SC clients but completely
# dead. bridge.py's own reader-presence polling can't catch this - pcscd itself never
# removes the reader from its list, so `readers()` keeps reporting it as there. A
# handful of these errors in a row means it's not going to recover on its own.
(
    stuck_count=0
    while IFS= read -r line; do
        echo "$line"
        case "$line" in
            *LIBUSB_ERROR_NO_DEVICE*)
                stuck_count=$((stuck_count + 1))
                if [ "$stuck_count" -ge 20 ]; then
                    echo "pcscd stuck retrying a disconnected reader - restarting container" >&2
                    : > "$RESTART_MARKER"
                    kill -TERM "$PCSCD_PID" 2>/dev/null || true
                    exit 0
                fi
                ;;
            *)
                stuck_count=0
                ;;
        esac
    done < "$PCSCD_LOG_FIFO"
) &
LOGWATCH_PID=$!

# give pcscd a moment to enumerate the reader before the client connects
sleep 2

python3 /app/bridge.py &
APP_PID=$!

# Covers the other failure mode: pcscd dying outright (crash, not just wedging - see
# the log watcher above for that case). bridge.py only talks to pcscd over PC/SC, it
# can't revive the daemon itself either way, so both watchers land on the same
# response: exit the whole container so Docker's `restart: unless-stopped` (see
# docker-compose.yml) gives it a clean slate instead of quietly running on with no
# working reader.
# `wait $PCSCD_PID` would NOT work here even though it reads naturally - `wait` in
# POSIX sh only blocks on a *direct child of the current shell process*, and pcscd is a
# child of the top-level entrypoint script, not of this subshell (a sibling, started by
# `&` in the same parent). In dash, waiting on a non-child PID returns immediately
# (rc=127) instead of blocking - combined with `set -e` (inherited into the subshell),
# that silently killed this whole watcher at startup, long before pcscd ever died,
# without any error surfacing anywhere. Polling liveness via `kill -0` instead works
# regardless of the parent/child relationship.
(
    while kill -0 "$PCSCD_PID" 2>/dev/null; do
        sleep 1
    done
    if [ ! -e "$RESTART_MARKER" ]; then
        echo "pcscd exited unexpectedly - restarting container" >&2
    fi
    kill -TERM "$APP_PID" 2>/dev/null || true
) &
WATCHER_PID=$!

wait "$APP_PID"
kill -TERM "$LOGWATCH_PID" "$WATCHER_PID" 2>/dev/null || true
rm -f "$PCSCD_LOG_FIFO" "$RESTART_MARKER"
