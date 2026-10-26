#!/bin/bash
# Runs on the HOST (not in a container) as coffeepy-update-watcher.service.
# Watches data/updates/incoming/ for bundles uploaded via the dashboard
# (/dashboard/update/) - a tar containing images.tar (a `docker load`-able image bundle)
# and docker-compose.yml, produced by scripts/build_update_bundle.sh. Unpacks it, loads
# the images, drops the new docker-compose.yml into place (backing up the old one first
# - this is also how a new container, i.e. a new service block, gets onto the Pi at
# all), brings the stack up again so compose recreates whichever services actually
# changed and removes any service dropped from the file, and writes a result log the
# dashboard polls for status.
#
# This is deliberately the only piece of the system with real Docker/host privileges -
# the web container itself never gets access to docker.sock (see the plan's "Offline
# update pipeline" section for why).
set -u

PROJECT_DIR="${COFFEEPY_DIR:-/opt/coffeepy}"
INCOMING_DIR="$PROJECT_DIR/data/updates/incoming"
PROCESSED_DIR="$PROJECT_DIR/data/updates/processed"
STATUS_DIR="$PROJECT_DIR/data/updates/status"

mkdir -p "$INCOMING_DIR" "$PROCESSED_DIR" "$STATUS_DIR"

# The kiosk shows "back in a moment" while a bundle is applied (see maintenance.sh).
MAINTENANCE_DIR="$PROJECT_DIR/data/maintenance"
# shellcheck source=maintenance.sh
. "$(dirname "$0")/maintenance.sh"

# One line for the app's audit log (dashboard/audit.py collects data/storage/events.jsonl).
# Bundle names are plain file names; anything that would break the JSON is dropped.
audit_event() {
    local target="${2//[^A-Za-z0-9._-]/}"
    mkdir -p "$PROJECT_DIR/data/storage"
    printf '{"time": %s, "action": "%s", "actor": "update watcher", "target": "%s", "outcome": "%s"}\n' \
        "$(date +%s)" "$1" "$target" "$3" >> "$PROJECT_DIR/data/storage/events.jsonl"
}

# Every failure path returns rather than exits: this runs inside the `inotifywait | while`
# pipeline's subshell, where an exit ends the loop for good while inotifywait - and with it
# the service - stays up. One broken upload used to silently swallow every later update.
process_bundle() {
    bundle="$1"
    name="$(basename "$bundle")"
    log="$STATUS_DIR/${name}.log"
    # The bundle's images.tar is several hundred MB, hence a work dir on the data partition:
    # /tmp is a RAM-backed tmpfs on current Raspberry Pi OS and it does not fit.
    workdir="$(mktemp -d "$PROJECT_DIR/data/updates/.work.XXXXXX")"

    maintenance_begin update
    audit_event update.start "$name" success
    if apply_bundle >"$log" 2>&1; then
        mv "$bundle" "$PROCESSED_DIR/$name"
        audit_event update.apply "$name" success
    else
        # Out of incoming/ either way, so a failed bundle is not tried again forever.
        mv "$bundle" "$PROCESSED_DIR/$name.failed"
        audit_event update.apply "$name" failure
    fi
    maintenance_end
    rm -rf "$workdir"
}

apply_bundle() {
    maintenance_step unpack 10
    echo "[$(date -Is)] Unpacking $name ..."
    if ! tar -xf "$bundle" -C "$workdir"; then
        echo "[$(date -Is)] FAILED: could not unpack bundle"
        return 1
    fi

    maintenance_step images 35
    echo "[$(date -Is)] Loading images ..."
    if ! docker load -i "$workdir/images.tar"; then
        echo "[$(date -Is)] FAILED: docker load"
        return 1
    fi

    if [ -f "$workdir/docker-compose.yml" ]; then
        backup="$PROJECT_DIR/docker-compose.yml.bak-$(date +%Y%m%d%H%M%S)"
        echo "[$(date -Is)] Updating docker-compose.yml (previous version kept at $backup) ..."
        cp "$PROJECT_DIR/docker-compose.yml" "$backup" 2>/dev/null || true
        cp "$workdir/docker-compose.yml" "$PROJECT_DIR/docker-compose.yml"
    fi

    maintenance_step restart 75
    echo "[$(date -Is)] Recreating any service whose image or definition changed ..."
    if ! (cd "$PROJECT_DIR" && docker compose up -d --remove-orphans); then
        echo "[$(date -Is)] FAILED: docker compose up"
        return 1
    fi

    # The images the update replaced are now untagged; each is a few hundred MB of SD card.
    docker image prune -f >/dev/null 2>&1 || true

    echo "[$(date -Is)] Update applied successfully."
}

echo "coffeepy-update-watcher: watching $INCOMING_DIR"

# inotify only reports what happens from now on - a bundle uploaded while the watcher was
# down (a reboot, a restart) would otherwise sit in incoming/ forever. The upload's own
# .uploading-* temp file does not match these patterns.
for bundle in "$INCOMING_DIR"/*.tar "$INCOMING_DIR"/*.tar.gz; do
    [ -e "$bundle" ] && process_bundle "$bundle"
done

if command -v inotifywait >/dev/null 2>&1; then
    inotifywait -m -e close_write -e moved_to --format '%f' "$INCOMING_DIR" | while read -r fname; do
        [ -f "$INCOMING_DIR/$fname" ] && process_bundle "$INCOMING_DIR/$fname"
    done
else
    # Fallback if inotify-tools isn't installed: poll every 5s.
    while true; do
        for bundle in "$INCOMING_DIR"/*.tar "$INCOMING_DIR"/*.tar.gz; do
            [ -e "$bundle" ] || continue
            process_bundle "$bundle"
        done
        sleep 5
    done
fi
