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

process_bundle() {
    bundle="$1"
    name="$(basename "$bundle")"
    log="$STATUS_DIR/${name}.log"
    workdir="$(mktemp -d)"

    {
        echo "[$(date -Is)] Unpacking $name ..."
        if ! tar -xf "$bundle" -C "$workdir"; then
            echo "[$(date -Is)] FAILED: could not unpack bundle"
            rm -rf "$workdir"
            exit 1
        fi

        echo "[$(date -Is)] Loading images ..."
        if ! docker load -i "$workdir/images.tar"; then
            echo "[$(date -Is)] FAILED: docker load"
            rm -rf "$workdir"
            exit 1
        fi

        if [ -f "$workdir/docker-compose.yml" ]; then
            backup="$PROJECT_DIR/docker-compose.yml.bak-$(date +%Y%m%d%H%M%S)"
            echo "[$(date -Is)] Updating docker-compose.yml (previous version kept at $backup) ..."
            cp "$PROJECT_DIR/docker-compose.yml" "$backup" 2>/dev/null || true
            cp "$workdir/docker-compose.yml" "$PROJECT_DIR/docker-compose.yml"
        fi

        echo "[$(date -Is)] Recreating any service whose image or definition changed ..."
        if ! (cd "$PROJECT_DIR" && docker compose up -d --remove-orphans); then
            echo "[$(date -Is)] FAILED: docker compose up"
            rm -rf "$workdir"
            exit 1
        fi

        echo "[$(date -Is)] Update applied successfully."
    } >"$log" 2>&1

    rm -rf "$workdir"
    mv "$bundle" "$PROCESSED_DIR/$name"
}

echo "coffeepy-update-watcher: watching $INCOMING_DIR"

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
