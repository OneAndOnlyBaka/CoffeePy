#!/usr/bin/env python3
"""Runs on the HOST (not in a container) as coffeepy-update-watcher.service: applies the
update bundles uploaded via the dashboard (/dashboard/update/), and rolls them back.

A bundle (scripts/build_update_bundle.sh) is a tar with
  manifest.json      version, build time, platform, needed space, new .env defaults, and the
                     SHA-256 of every other file
  manifest.sig       Ed25519 signature of manifest.json - checked against the public key the
                     installer put in /etc/coffeepy/update-signing.pub (outside data/, where
                     the app can't change it), or the device's own one next to it
                     (update-signing.own.pub, from scripts/provision.sh), which no update
                     replaces. UPDATE_VERIFY_SIGNATURE=false in .env turns the check off.
  images.tar         every image docker-compose.yml needs, `docker save`d
  docker-compose.yml
  install.tar        install/ - this watcher, the storage helper, systemd units, the kiosk
                     session: the parts of the device the images don't carry (optional)
  os.tar             Raspberry Pi OS packages from install/packages.txt, for an offline
                     device's security updates (optional)

Before anything changes, the bundle is checked: signature and checksums, platform, free
space, and that it isn't older than what runs (unless the upload allowed a downgrade). Then
the database is saved to the SD card (data/updates/pre-update/ - never the backup stick,
which belongs to the backups) and the running images are tagged :coffeepy-previous. After
`docker compose up`, the app has to answer /health/ within HEALTH_TIMEOUT; if it doesn't,
the old images, compose file, install/ and database go back automatically. The dashboard
can ask for the same rollback by hand (incoming/rollback.request).

This is deliberately the only piece of the system with Docker on the host - the web
container never gets docker.sock. It talks to the dashboard through files in data/updates/:
state.json (what it is doing), installed.json (what is installed), status/<bundle>.log.
Python's standard library only: it runs on the plain Raspberry Pi OS python3.
"""
import hashlib
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("COFFEEPY_DIR", "/opt/coffeepy"))
UPDATES = PROJECT_DIR / "data" / "updates"
INCOMING = UPDATES / "incoming"
STATUS_DIR = UPDATES / "status"
PRE_UPDATE = UPDATES / "pre-update"
STATE_FILE = UPDATES / "state.json"
INSTALLED_FILE = UPDATES / "installed.json"
PREVIOUS_FILE = UPDATES / "previous.json"
SYSTEM_FILE = UPDATES / "system.json"
MAINTENANCE_FILE = PROJECT_DIR / "data" / "maintenance" / "status.json"
EVENTS_FILE = PROJECT_DIR / "data" / "storage" / "events.jsonl"
PUBLIC_KEY = Path(os.environ.get("COFFEEPY_UPDATE_PUBKEY", "/etc/coffeepy/update-signing.pub"))
OWN_PUBLIC_KEY = PUBLIC_KEY.with_name("update-signing.own.pub")
ROLLBACK_REQUEST = "rollback.request"
FORMAT = 2
KEEP_PRE_UPDATE = 3
HEALTH_TIMEOUT = 240
HEALTH_URL = "https://localhost:8443/health/"
STALE_UPLOAD_SECONDS = 3600
POLL_SECONDS = 5
SYSTEM_SECONDS = 600  # system.json is refreshed this often - an install.sh run rebuilds images too
PREVIOUS_TAG = "coffeepy-previous"
# uname -m -> the platform a bundle is built for (docker's names)
PLATFORMS = {"aarch64": "linux/arm64", "arm64": "linux/arm64", "armv7l": "linux/arm/v7", "x86_64": "linux/amd64"}


class UpdateError(Exception):
    """Why an update was refused or failed - written to the log and shown in the dashboard."""


def log(message):
    print(message, flush=True)


def run(*args, stdin=None, cwd=None, check=True):
    """A command; with check, its failure raises UpdateError with what it said."""
    env = {**os.environ, "COFFEEPY_FROM_WATCHER": "1"}  # see install.sh's services step
    result = subprocess.run(args, input=stdin, cwd=cwd, capture_output=True, env=env)
    if check and result.returncode != 0:
        output = (result.stderr or result.stdout).decode(errors="replace").strip()
        raise UpdateError(f"{' '.join(str(a) for a in args[:3])}: {output[-400:]}")
    return result


def compose(*args, **kwargs):
    return run("docker", "compose", *args, cwd=PROJECT_DIR, **kwargs)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def event(action, target="", details="", outcome="success", actor="update watcher"):
    """One line for the app's audit log (dashboard/audit.py collects events.jsonl)."""
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps({"time": time.time(), "action": action, "actor": actor, "target": target,
                                "details": details, "outcome": outcome}) + "\n")
    except OSError as exc:
        log(f"event {action}: {exc}")


class Job:
    """One run - an update or a rollback: its log file, the dashboard's state.json and the
    kiosk's maintenance screen, kept in step."""

    def __init__(self, name, kind="update"):
        self.name, self.kind = name, kind
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path = STATUS_DIR / f"{name}.log"
        self.log_path.write_text("")

    def say(self, message):
        line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}"
        log(line)
        with open(self.log_path, "a") as f:
            f.write(line + "\n")

    def step(self, step, percent, message):
        self.say(message)
        write_json(STATE_FILE, {"state": "running", "kind": self.kind, "bundle": self.name, "step": step,
                                "percent": percent, "message": message, "updated": time.time()})
        write_json(MAINTENANCE_FILE, {"kind": self.kind, "step": step, "percent": percent,
                                      "updated": int(time.time())})

    def finish(self, state, message, **extra):
        self.say(message)
        write_json(STATE_FILE, {"state": state, "kind": self.kind, "bundle": self.name, "message": message,
                                "updated": time.time(), **extra})
        MAINTENANCE_FILE.unlink(missing_ok=True)


# --- what runs now ------------------------------------------------------------------------

def host_platform():
    return PLATFORMS.get(platform.machine(), platform.machine())


def installed():
    return read_json(INSTALLED_FILE, {}) or {}


def env_values():
    values = {}
    try:
        lines = (PROJECT_DIR / ".env").read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def add_env_defaults(defaults):
    """Settings a new version needs that this device's .env doesn't have yet: added with the
    bundle's default. What is there already is never changed."""
    have = env_values()
    missing = {k: v for k, v in (defaults or {}).items() if k not in have}
    if missing:
        with open(PROJECT_DIR / ".env", "a") as f:
            f.write("\n# Added by update\n" + "".join(f"{k}={v}\n" for k, v in missing.items()))
    return sorted(missing)


def other_maintenance():
    """Something else has the device in maintenance (a restore, an install) - wait for it."""
    status = read_json(MAINTENANCE_FILE)
    if not status or time.time() - status.get("updated", 0) > 2 * 3600:
        return None
    return status.get("kind") if status.get("kind") not in ("update", "rollback") else None


# --- checking a bundle --------------------------------------------------------------------

def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(bundle, workdir):
    """manifest.json (and its signature) out of the bundle, checked; nothing else is
    unpacked before this passed."""
    with tarfile.open(bundle) as tar:
        names = tar.getnames()
        if "manifest.json" not in names:
            raise UpdateError("This is not an update bundle of this version (no manifest.json) - "
                              "build it again with scripts/build_update_bundle.sh.")
        tar.extractall(workdir, members=[tar.getmember(n) for n in ("manifest.json", "manifest.sig")
                                         if n in names], filter="data")
    verify_signature(workdir / "manifest.json", workdir / "manifest.sig")
    manifest = read_json(workdir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise UpdateError(f"Unsupported bundle format {manifest.get('format') if isinstance(manifest, dict) else '?'}.")
    return manifest


def verify_signature(manifest, signature):
    if env_values().get("UPDATE_VERIFY_SIGNATURE", "true").lower() == "false":
        log("Signature check turned off on this device (UPDATE_VERIFY_SIGNATURE=false) - not checked")
        return
    if not signature.exists():
        raise UpdateError("The bundle is not signed - refused (UPDATE_VERIFY_SIGNATURE=false in .env turns the "
                          "check off).")
    key = OWN_PUBLIC_KEY if OWN_PUBLIC_KEY.exists() else PUBLIC_KEY
    if not key.exists():
        raise UpdateError(f"No public key at {PUBLIC_KEY} to check the signature with - run the installer.")
    run("openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key), "-rawin",
        "-in", str(manifest), "-sigfile", str(signature))


def check_bundle(manifest, options, bundle):
    """Everything that can be known before touching the device."""
    if manifest.get("platform") != host_platform():
        raise UpdateError(f"The bundle is for {manifest.get('platform')}, this device is {host_platform()}.")
    free_mb = shutil.disk_usage(PROJECT_DIR).free // (1 << 20)
    needed = int(manifest.get("required_mb", 0)) + bundle.stat().st_size // (1 << 20)
    if free_mb < needed:
        raise UpdateError(f"Not enough space: {free_mb} MB free, {needed} MB needed.")
    current = installed().get("built_at", 0)
    if manifest.get("built_at", 0) < current and not options.get("allow_downgrade"):
        raise UpdateError(f"The bundle ({manifest.get('version')}) is older than what is installed "
                          f"({installed().get('version')}) - allow the downgrade to install it anyway.")


def unpack(bundle, manifest, workdir):
    with tarfile.open(bundle) as tar:
        tar.extractall(workdir, filter="data")
    for name, digest in manifest.get("files", {}).items():
        path = workdir / name
        if not path.is_file() or sha256_of(path) != digest:
            raise UpdateError(f"{name} in the bundle is damaged or missing (checksum).")


# --- saving what runs, and putting it back ------------------------------------------------

def db_credentials():
    values = env_values()
    return values.get("POSTGRES_USER", "coffeepy"), values.get("POSTGRES_DB", "coffeepy")


def save_database(label):
    """The database onto the SD card, through the db container's own pg_dump (the same
    version as the server). The newest KEEP_PRE_UPDATE are kept."""
    PRE_UPDATE.mkdir(parents=True, exist_ok=True)
    user, db = db_credentials()
    target = PRE_UPDATE / f"{label}_{time.strftime('%Y-%m-%d_%H%M%S')}.dump"
    result = compose("exec", "-T", "db", "pg_dump", "-U", user, "--format", "custom", db)
    target.write_bytes(result.stdout)
    for old in sorted(PRE_UPDATE.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_PRE_UPDATE:]:
        old.unlink()
    return target


def load_database(dump):
    """`dump` back, as one transaction - with the app's containers stopped, so nothing holds
    a lock or writes in between."""
    user, db = db_credentials()
    script = ("pg_restore --no-owner --no-privileges -f - | (echo 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;';"
              f" cat) | psql -v ON_ERROR_STOP=1 --single-transaction -q -U {user} -d {db}")
    compose("exec", "-T", "db", "sh", "-c", script, stdin=dump.read_bytes())


def current_images():
    return sorted(set(compose("config", "--images").stdout.decode().split()))


def previous_tag(image):
    repo, _, tag = image.rpartition(":")
    return f"{repo}:{PREVIOUS_TAG}-{tag}" if repo else f"{image}:{PREVIOUS_TAG}"


def snapshot(job):
    """What a rollback puts back: the images (tagged so prune leaves them), the compose file,
    install/, the database, and what was installed."""
    images = current_images()
    for image in images:
        run("docker", "tag", image, previous_tag(image))
    shutil.copyfile(PROJECT_DIR / "docker-compose.yml", UPDATES / "docker-compose.yml.previous")
    install = PROJECT_DIR / "install"
    shutil.rmtree(UPDATES / "install.previous", ignore_errors=True)
    if install.is_dir():
        shutil.copytree(install, UPDATES / "install.previous", symlinks=True)
    dump = save_database(f"before-{installed().get('version', 'unknown')}")
    write_json(PREVIOUS_FILE, {"images": images, "database": dump.name, "installed": installed(),
                               "saved": time.time()})
    job.say(f"Saved the running version: {len(images)} image(s), database {dump.name}")


def rollback(job, reason):
    """Back to what snapshot() saved. Raises UpdateError if there is nothing to go back to."""
    previous = read_json(PREVIOUS_FILE)
    if not previous:
        raise UpdateError("There is no previous version to go back to.")
    job.step("rollback", 50, f"Going back to the previous version: {reason}")
    for image in previous["images"]:
        run("docker", "tag", previous_tag(image), image)
    shutil.copyfile(UPDATES / "docker-compose.yml.previous", PROJECT_DIR / "docker-compose.yml")
    if (UPDATES / "install.previous").is_dir():
        shutil.rmtree(PROJECT_DIR / "install", ignore_errors=True)
        shutil.copytree(UPDATES / "install.previous", PROJECT_DIR / "install", symlinks=True)
    compose("stop", "web", "mqtt-listener", "backup", check=False)
    load_database(PRE_UPDATE / previous["database"])
    compose("up", "-d", "--remove-orphans")
    if not healthy():
        raise UpdateError("The previous version does not come up either - see the log.")
    write_json(INSTALLED_FILE, previous["installed"])
    PREVIOUS_FILE.unlink()


def healthy(timeout=None):
    """The app answers /health/ - through Caddy, with the device CA, like the kiosk does."""
    timeout = HEALTH_TIMEOUT if timeout is None else timeout
    context = ssl.create_default_context(cafile=str(PROJECT_DIR / "data" / "tls" / "ca.crt"))
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(HEALTH_URL, context=context, timeout=10) as response:
                if response.status == 200:
                    return True
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(3)


# --- applying -----------------------------------------------------------------------------

def apply(job, manifest, workdir):
    if (workdir / "os.tar").is_file():
        job.step("packages", 20, "Installing the operating system updates ...")
        with tarfile.open(workdir / "os.tar") as tar:
            tar.extractall(workdir / "os", filter="data")
        run(str(PROJECT_DIR / "install" / "install.sh"), "--only=packages", f"--offline={workdir / 'os'}", "--yes")

    job.step("images", 35, "Loading the new images ...")
    run("docker", "load", "-i", str(workdir / "images.tar"))

    added = add_env_defaults(manifest.get("env_defaults"))
    if added:
        job.say(f"New settings with their defaults: {', '.join(added)}")
    shutil.copyfile(workdir / "docker-compose.yml", PROJECT_DIR / "docker-compose.yml")
    if (workdir / "install.tar").is_file():
        job.say("Updating install/ ...")
        shutil.rmtree(PROJECT_DIR / "install", ignore_errors=True)
        with tarfile.open(workdir / "install.tar") as tar:
            tar.extractall(PROJECT_DIR, filter="fully_trusted")  # signed - and needs its executable bits

    job.step("restart", 60, "Starting the new version ...")
    compose("up", "-d", "--remove-orphans")
    job.step("verify", 80, "Waiting for the new version to answer ...")
    if not healthy():
        raise UpdateError("The new version did not answer within "
                          f"{HEALTH_TIMEOUT} seconds.")
    if (workdir / "install.tar").is_file():
        job.step("services", 90, "Updating the device's services ...")
        run(str(PROJECT_DIR / "install" / "install.sh"), "--only=hardware,services,kiosk", "--yes")


def process_bundle(bundle):
    name = bundle.name
    if other_maintenance():
        log(f"{name}: waiting - the device is busy with: {other_maintenance()}")
        return False
    options_file = bundle.with_name(bundle.name + ".options.json")
    options = read_json(options_file, {}) or {}
    job = Job(name)
    job.step("starting", 5, f"Checking {name} ...")
    event("update.start", name, actor=options.get("requested_by", "update watcher"))
    workdir = Path(tempfile.mkdtemp(dir=UPDATES, prefix=".work-"))
    snapshotted = False
    try:
        manifest = read_manifest(bundle, workdir)
        check_bundle(manifest, options, bundle)
        job.say(f"Bundle {manifest.get('version')} for {manifest.get('platform')}: signature and checks passed")
        unpack(bundle, manifest, workdir)
        job.step("unpack", 15, "Saving the running version ...")
        snapshot(job)
        snapshotted = True
        apply(job, manifest, workdir)
        write_json(INSTALLED_FILE, {"version": manifest.get("version"), "built_at": manifest.get("built_at"),
                                    "applied_at": time.time()})
        run("docker", "image", "prune", "-f", check=False)  # untagged leftovers; the previous ones are tagged
        job.finish("done", f"Update to {manifest.get('version')} applied.", version=manifest.get("version"))
        event("update.apply", name, details=str(manifest.get("version")))
        restart_self = (workdir / "install.tar").is_file()
    except Exception as exc:  # noqa: BLE001 - every failure ends in the log and the dashboard
        job.say(f"FAILED: {exc}")
        restart_self = False
        if snapshotted:
            try:
                rollback(job, str(exc))
                job.finish("rolled_back", f"Update failed and was rolled back: {exc}")
                event("update.apply", name, details=f"rolled back: {exc}", outcome="failure")
            except Exception as again:  # noqa: BLE001
                job.finish("failed", f"Update failed, and so did the rollback: {again}")
                event("update.rollback", name, details=str(again), outcome="failure")
        else:
            job.finish("failed", f"Update refused: {exc}")
            event("update.apply", name, details=str(exc), outcome="failure")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        # Out of incoming/ either way: applied, it is no longer needed (the rollback has its
        # own copies); refused, it would only be tried again forever.
        bundle.unlink(missing_ok=True)
        options_file.unlink(missing_ok=True)
    if restart_self:
        # install/ brought a new version of this very script: hand over to it.
        run("systemctl", "restart", "--no-block", "coffeepy-update-watcher.service", check=False)
    return True


def process_rollback_request(request):
    asked = read_json(request, {}) or {}
    request.unlink(missing_ok=True)
    job = Job(f"rollback-{time.strftime('%Y%m%d%H%M%S')}", kind="rollback")
    job.step("starting", 5, "Rollback asked for in the dashboard")
    try:
        rollback(job, f"asked for by {asked.get('requested_by', 'an admin')}")
        job.finish("rolled_back", "Rolled back to the previous version.")
        event("update.rollback", details="by hand", actor=asked.get("requested_by", "update watcher"))
    except Exception as exc:  # noqa: BLE001
        job.finish("failed", f"Rollback failed: {exc}")
        event("update.rollback", details=str(exc), outcome="failure", actor=asked.get("requested_by", "update watcher"))


def cleanup_uploads():
    """Leftovers of uploads that never finished (a timeout, the WiFi gone)."""
    for path in INCOMING.glob(".uploading-*"):
        if time.time() - path.stat().st_mtime > STALE_UPLOAD_SECONDS:
            path.unlink(missing_ok=True)


# --- what the dashboard's version list shows ------------------------------------------------

def _output(*args, cwd=None):
    try:
        result = run(*args, cwd=cwd, check=False)
    except OSError:
        return ""
    return result.stdout.decode(errors="replace").strip() if result.returncode == 0 else ""


def _containers():
    """The compose project's containers: service, image and the image's id. The id is what
    tells two builds apart that carry the same tag (coffeepy-web:latest, caddy:2-alpine)."""
    raw = _output("docker", "compose", "ps", "--all", "--format", "json", cwd=PROJECT_DIR)
    try:  # compose prints one object per line; older releases a single array
        rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines() if line]
    except ValueError:
        return []
    ids = [row.get("ID", "") for row in rows if row.get("ID")]
    image_ids = {}
    if ids:
        for line in _output("docker", "inspect", "--format", "{{.Id}} {{.Image}}", *ids).splitlines():
            container, _, image = line.partition(" ")
            image_ids[container] = image
    out = []
    for row in rows:
        short = row.get("ID") or "-"  # compose shows the container id abbreviated, inspect in full
        image_id = next((image for container, image in image_ids.items() if container.startswith(short)), "")
        out.append({"service": row.get("Service", ""), "image": row.get("Image", ""),
                    "image_id": image_id.removeprefix("sha256:")[:12], "state": row.get("State", "")})
    return sorted(out, key=lambda c: c["service"])


def _os_release():
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.partition("=")[2].strip('"')
    except OSError:
        pass
    return ""


def _signature():
    if env_values().get("UPDATE_VERIFY_SIGNATURE", "true").lower() == "false":
        return {"check": False}
    key = OWN_PUBLIC_KEY if OWN_PUBLIC_KEY.exists() else PUBLIC_KEY
    if not key.exists():
        return {"check": True, "key": "missing"}
    return {"check": True, "key": "own" if key == OWN_PUBLIC_KEY else "bundled",
            "fingerprint": hashlib.sha256(key.read_bytes()).hexdigest()[:16]}


def write_system_info():
    try:
        model = Path("/proc/device-tree/model").read_text().strip("\x00\n ")
    except OSError:
        model = ""
    write_json(SYSTEM_FILE, {
        "updated": time.time(), "model": model, "os": _os_release(), "kernel": platform.release(),
        "platform": host_platform(), "docker": _output("docker", "version", "--format", "{{.Server.Version}}"),
        "compose": _output("docker", "compose", "version", "--short"), "containers": _containers(),
        "signature": _signature(),
    })


def cycle(last_system=0.0):
    """One look at incoming/; returns when system.json was last written."""
    cleanup_uploads()
    changed = False
    request = INCOMING / ROLLBACK_REQUEST
    if request.exists() and not other_maintenance():
        process_rollback_request(request)
        changed = True
    for bundle in sorted(INCOMING.glob("*.tar")) + sorted(INCOMING.glob("*.tar.gz")):
        process_bundle(bundle)
        changed = True
    if changed or time.time() - last_system >= SYSTEM_SECONDS:
        write_system_info()
        return time.time()
    return last_system


def main(once=False):
    for directory in (INCOMING, STATUS_DIR, PRE_UPDATE):
        directory.mkdir(parents=True, exist_ok=True)
    log(f"coffeepy-update-watcher: watching {INCOMING}")
    last_system = 0.0
    while True:
        try:
            last_system = cycle(last_system)
        except Exception as exc:  # noqa: BLE001 - keep watching
            log(f"cycle failed: {exc}")
        if once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
