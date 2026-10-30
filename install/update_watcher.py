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
space, and that it isn't older than what runs - with one exception: a device on a pre-release
may go back to a release of the same major version (back_to_release). Then
the running images are tagged :coffeepy-previous, the new ones loaded, and - with everything
that writes stopped, so no booking falls between the dump and the switch - the database is
saved to the SD card (data/updates/pre-update/ - never the backup stick, which belongs to the
backups) as plain SQL, gzipped: `gunzip -c <file> | psql` puts it back with any psql, by hand
if need be. Migrations the bundle doesn't know (that release, say) are taken back by the
running version first (migrate_back). After `docker compose up`, the app has to answer /health/ within HEALTH_TIMEOUT;
if it doesn't, the old images, compose file, install/ and database go back automatically. Once
it does, there is no way back: the copies of the old version are deleted (forget_previous) -
what is wrong with a version that came up is fixed by the next update, never by the last one.

A power cut can strike at any of these steps, so each is recorded in journal.json *before* it
changes anything (and the bundle waits in active/ meanwhile). At the next boot
coffeepy-update-recover.service runs `update_watcher.py --recover` before the stack starts -
not the watcher itself, which only starts after the stack, and a stack that can't start would
keep it from ever repairing anything. It takes the device back to the version before the
update and puts the bundle back into incoming/, so the update runs again from the start - at
most MAX_ATTEMPTS times. Every file the device needs after a reboot is replaced so that a
power cut leaves the old one or the new one, never half of either (write_file, replace_dir).

This is deliberately the only piece of the system with Docker on the host - the web
container never gets docker.sock. It talks to the dashboard through files in data/updates/:
state.json (what it is doing), installed.json (what is installed), status/<bundle>.log.
Python's standard library only: it runs on the plain Raspberry Pi OS python3.
"""
import ctypes
import gzip
import hashlib
import json
import os
import platform
import re
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
ACTIVE = UPDATES / "active"
JOURNAL_FILE = UPDATES / "journal.json"
STATE_FILE = UPDATES / "state.json"
INSTALLED_FILE = UPDATES / "installed.json"
PREVIOUS_FILE = UPDATES / "previous.json"
SYSTEM_FILE = UPDATES / "system.json"
MAINTENANCE_FILE = PROJECT_DIR / "data" / "maintenance" / "status.json"
EVENTS_FILE = PROJECT_DIR / "data" / "storage" / "events.jsonl"
PUBLIC_KEY = Path(os.environ.get("COFFEEPY_UPDATE_PUBKEY", "/etc/coffeepy/update-signing.pub"))
OWN_PUBLIC_KEY = PUBLIC_KEY.with_name("update-signing.own.pub")
ROLLBACK_REQUEST = "rollback.request"  # what an older dashboard asked a rollback by hand with
FORMAT = 2
KEEP_PRE_UPDATE = 3
HEALTH_TIMEOUT = 240
HEALTH_URL = "https://localhost:8443/health/"
STALE_UPLOAD_SECONDS = 3600
POLL_SECONDS = 5
SYSTEM_SECONDS = 600  # system.json is refreshed this often - an install.sh run rebuilds images too
PREVIOUS_TAG = "coffeepy-previous"
WEB_IMAGE = "coffeepy-web:latest"  # what every Django container runs (docker-compose.yml)
MAX_ATTEMPTS = 3  # an update a power cut interrupted runs again - but not forever
# The steps an update records in journal.json, in order. Up to "saved" the old version's
# compose file, install/ and database are untouched; from "switched" on they have to go back.
PHASES = ("accepted", "tagged", "loaded", "saved", "switched")
WRITERS = ("web", "mqtt-listener", "backup")  # what changes the database while the stack runs
# The last thing pg_dump writes into a plain dump (newer releases add a \unrestrict line after it).
DUMP_COMPLETE = b"-- PostgreSQL database dump complete"
AT_FDCWD, RENAME_EXCHANGE = -100, 2
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


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path, data, mode=0o644):
    """`data` into `path` so that a power cut leaves either the old file or the new one: under
    a temporary name, synced, renamed, and the rename synced too - on an SD card a renamed but
    unsynced file can come back empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    sync_dir(path.parent)


def write_json(path, data):
    write_file(path, json.dumps(data, indent=1).encode())


def _exchange(a, b):
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise OSError("renameat2 is not available")
    if renameat2(AT_FDCWD, os.fsencode(a), AT_FDCWD, os.fsencode(b), RENAME_EXCHANGE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def replace_dir(new, target):
    """`new` becomes `target` in one step: install/ holds this very script and the recovery
    unit's, and a power cut between removing the old one and moving the new one in would
    leave the device with neither. `new` is gone afterwards."""
    if not target.exists():
        os.rename(new, target)
    else:
        try:
            _exchange(new, target)
        except OSError:
            # A kernel or filesystem without RENAME_EXCHANGE: the old way, with its moment of no target.
            shutil.rmtree(target)
            os.rename(new, target)
        else:
            shutil.rmtree(new)
    sync_dir(target.parent)


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

    def __init__(self, name, kind="update", fresh=True):
        self.name, self.kind = name, kind
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path = STATUS_DIR / f"{name}.log"
        if fresh:  # a recovery and the attempts after it continue the first attempt's log
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
        env = PROJECT_DIR / ".env"
        text = env.read_text() if env.exists() else ""
        text += "\n# Added by update\n" + "".join(f"{k}={v}\n" for k, v in missing.items())
        write_file(env, text.encode(), mode=env.stat().st_mode & 0o777 if env.exists() else 0o600)
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


def parse_version(version):
    """(major, pre-release?) of a SemVer - 2.1.0-feature-x.5, or git describe's v2.0.2-7-gabc+dirty -
    None for anything else."""
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(-[^+]+)?(\+.*)?", str(version or ""))
    return (int(match[1]), bool(match[2])) if match else None


def back_to_release(running, bundle):
    """A pre-release may give way to a release although that is older: the way off a test build.
    Not across a major version, whose data may not go back."""
    now, then = parse_version(running), parse_version(bundle)
    return bool(now and then and now[1] and not then[1] and now[0] == then[0])


def check_bundle(manifest, bundle):
    """Everything that can be known before touching the device."""
    if manifest.get("platform") != host_platform():
        raise UpdateError(f"The bundle is for {manifest.get('platform')}, this device is {host_platform()}.")
    free_mb = shutil.disk_usage(PROJECT_DIR).free // (1 << 20)
    needed = int(manifest.get("required_mb", 0)) + bundle.stat().st_size // (1 << 20)
    if free_mb < needed:
        raise UpdateError(f"Not enough space: {free_mb} MB free, {needed} MB needed.")
    current = installed()
    if (manifest.get("built_at", 0) < current.get("built_at", 0)
            and not back_to_release(current.get("version"), manifest.get("version"))):
        raise UpdateError(f"The bundle ({manifest.get('version')}) is older than what is installed "
                          f"({current.get('version')}) - updates only go forward.")


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
    """The database onto the SD card as gzipped plain SQL, through the db container's own
    pg_dump (the same version as the server), and checked to be complete. The newest
    KEEP_PRE_UPDATE are kept."""
    PRE_UPDATE.mkdir(parents=True, exist_ok=True)
    user, db = db_credentials()
    target = PRE_UPDATE / f"{label}_{time.strftime('%Y-%m-%d_%H%M%S')}.sql.gz"
    result = compose("exec", "-T", "db", "sh", "-c",
                     f"set -o pipefail; pg_dump -U {user} --format plain {db} | gzip -6")
    write_file(target, result.stdout)
    check_dump(target)
    dumps = [*PRE_UPDATE.glob("*.sql.gz"), *PRE_UPDATE.glob("*.dump")]  # .dump: an older watcher's
    for old in sorted(dumps, key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_PRE_UPDATE:]:
        old.unlink()
    return target


def check_dump(dump, sha256=None):
    """A plain dump only counts once it reads to its end: pg_dump closes it with its own
    comment, and a full card or a killed pg_dump leaves a file without. Measured: psql commits
    a dump cut off in the middle of its data, so this has to be known before loading it."""
    if sha256 and sha256_of(dump) != sha256:
        raise UpdateError(f"The saved database {dump.name} has changed since it was saved (checksum).")
    tail = b""
    try:
        with gzip.open(dump, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                tail = (tail + chunk)[-512:]
    except (OSError, EOFError) as exc:
        raise UpdateError(f"The saved database {dump.name} is damaged: {exc}") from exc
    if DUMP_COMPLETE not in tail:
        raise UpdateError(f"The saved database {dump.name} is incomplete.")


def load_database(dump):
    """`dump` back, all or nothing - with the app's containers stopped, so nothing holds a lock
    or writes in between. COMMIT is only sent once the whole dump was read: psql's own
    --single-transaction commits whatever it got when its input ends early."""
    user, db = db_credentials()
    script = ("set -o pipefail; (echo 'BEGIN; DROP SCHEMA public CASCADE; CREATE SCHEMA public;'; gunzip -c"
              f" && echo 'COMMIT;') | psql -v ON_ERROR_STOP=1 -q -U {user} -d {db}")
    compose("exec", "-T", "db", "sh", "-c", script, stdin=dump.read_bytes())


def current_images():
    return sorted(set(compose("config", "--images").stdout.decode().split()))


def previous_tag(image):
    repo, _, tag = image.rpartition(":")
    return f"{repo}:{PREVIOUS_TAG}-{tag}" if repo else f"{image}:{PREVIOUS_TAG}"


def journal(phase, fresh=False, **fields):
    """Records that the update is about to (or just did) reach `phase` - see PHASES."""
    data = {} if fresh else read_json(JOURNAL_FILE, {}) or {}
    write_json(JOURNAL_FILE, {**data, **fields, "phase": phase, "updated": time.time()})


def reached(data, phase):
    return data.get("phase") in PHASES and PHASES.index(data["phase"]) >= PHASES.index(phase)


def tag_images():
    """The running images, tagged so that `docker load` doesn't take their names and prune
    leaves them."""
    images = current_images()
    for image in images:
        run("docker", "tag", image, previous_tag(image))
    journal("tagged", images=images)


def save(job):
    """What a rollback puts back besides the images: the compose file, install/, the database
    and what was installed - taken with everything that writes stopped, so no booking made
    after the dump gets lost to a rollback."""
    compose("stop", *WRITERS)
    write_file(UPDATES / "docker-compose.yml.previous", (PROJECT_DIR / "docker-compose.yml").read_bytes())
    install = PROJECT_DIR / "install"
    shutil.rmtree(UPDATES / "install.previous", ignore_errors=True)
    if install.is_dir():
        shutil.copytree(install, UPDATES / "install.previous", symlinks=True)
    dump = save_database(f"before-{installed().get('version', 'unknown')}")
    images = (read_json(JOURNAL_FILE, {}) or {}).get("images", [])
    os.sync()  # the copies, not only the dump, have to be on the card before the journal says so
    write_json(PREVIOUS_FILE, {"images": images, "database": dump.name, "sha256": sha256_of(dump),
                               "installed": installed(), "saved": time.time()})
    journal("saved")
    job.say(f"Saved the running version: {len(images)} image(s), database {dump.name}")


def restore_install(source):
    """install/ back from `source` (a saved copy, which stays where it is)."""
    staged = UPDATES / ".install-restore"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(source, staged, symlinks=True)
    replace_dir(staged, PROJECT_DIR / "install")


def rollback(job, reason):
    """Back to what save() kept, while the update is still under way (undo() is the only caller).
    Can run again after a power cut cut it short: every step puts the same thing back."""
    previous = read_json(PREVIOUS_FILE)
    job.step("rollback", 50, f"Going back to the previous version: {reason}")
    dump = PRE_UPDATE / previous["database"]
    check_dump(dump, previous.get("sha256"))
    for image in previous["images"]:
        run("docker", "tag", previous_tag(image), image)
    write_file(PROJECT_DIR / "docker-compose.yml", (UPDATES / "docker-compose.yml.previous").read_bytes())
    if (UPDATES / "install.previous").is_dir():
        restore_install(UPDATES / "install.previous")
    compose("stop", *WRITERS, check=False)
    compose("up", "-d", "db")  # after a reboot - or a failed start - it need not be running
    load_database(dump)
    compose("up", "-d", "--remove-orphans")
    if not healthy():
        raise UpdateError("The previous version does not come up either - see the log.")
    write_json(INSTALLED_FILE, previous["installed"])
    PREVIOUS_FILE.unlink()


def undo(job, reason):
    """Back to the version before the update the journal describes, from whatever step it got
    to. Before "switched" only the image names moved (and the writers may be stopped); from
    there on rollback() puts everything back - unless it already did and only the journal
    didn't learn of it, in which case previous.json is gone."""
    data = read_json(JOURNAL_FILE, {}) or {}
    if reached(data, "switched") and PREVIOUS_FILE.exists():
        rollback(job, reason)
        return
    job.step("rollback", 50, f"Going back to the running version: {reason}")
    for image in data.get("images", []):
        run("docker", "tag", previous_tag(image), image)
    PREVIOUS_FILE.unlink(missing_ok=True)  # save()'s, if it got that far: the version that runs now
    compose("up", "-d", "--remove-orphans")
    if not healthy():
        raise UpdateError("The previous version does not come up either - see the log.")


def django_in(image, *command, network=None):
    """A one-off Django process in `image` with the device's settings - on the stack's network
    when it needs the database."""
    network_args = ("--network", network) if network else ()
    return run("docker", "run", "--rm", "--env-file", str(PROJECT_DIR / ".env"), "-e", "COFFEEPY_PREPARE=0",
               *network_args, image, *command)


# Every migration file the image's code has (a squashed one's originals too - no database
# needed), and the newest migration per app.
KNOWN_MIGRATIONS = ("import json, os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); "
                    "django.setup(); from django.db.migrations.loader import MigrationLoader; "
                    "loader = MigrationLoader(None, ignore_no_migrations=True); "
                    "print(json.dumps({'nodes': sorted(loader.disk_migrations), "
                    "'leaves': sorted(loader.graph.leaf_nodes())}))")


def migrate_back(job):
    """Migrations in the database that the new images don't know - a pre-release going back to
    its release: the running version, which does know them, takes them back. Otherwise the older
    code would start on tables it can't write to, and still answer /health/. Runs after the
    switch, so a migration that can't be reversed costs a rollback with the dump, nothing more."""
    known = json.loads(django_in(WEB_IMAGE, "python", "-c", KNOWN_MIGRATIONS).stdout.decode().splitlines()[-1])
    nodes = {tuple(node) for node in known["nodes"]}
    user, db = db_credentials()
    rows = compose("exec", "-T", "db", "psql", "-U", user, "-d", db, "-At", "-F", " ",
                   "-c", "select app, name from django_migrations").stdout.decode().splitlines()
    ahead = sorted({row.split(" ")[0] for row in rows if row and tuple(row.split(" ", 1)) not in nodes})
    if not ahead:
        return
    job.step("migrate", 50, "Taking the database back to what the new version knows ...")
    db_container = compose("ps", "-q", "db").stdout.decode().split()[0]
    network = run("docker", "inspect", "-f", "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}",
                  db_container).stdout.decode().split()[0]
    leaves = dict(known["leaves"])
    targets = [(app, leaves.get(app, "zero")) for app in ahead]  # an app the new version lacks goes to zero
    for app, target in targets:
        django_in(previous_tag(WEB_IMAGE), "python", "manage.py", "migrate", app, target, "--no-input",
                  network=network)
    job.say("Database migrations taken back: " + ", ".join(f"{app} to {target}" for app, target in targets))


def forget_previous():
    """The copies of the version before an update that came up: there is no way back to it, so
    they only take room - its images most of all (about 0.9 GB on a Pi). Untagged here, they go
    with the next prune; the dumps in pre-update/ stay, as copies of the data."""
    previous = read_json(PREVIOUS_FILE, {}) or {}
    for image in previous.get("images", []):
        run("docker", "image", "rm", previous_tag(image), check=False)  # only the tag, while another one holds it
    shutil.rmtree(UPDATES / "install.previous", ignore_errors=True)
    (UPDATES / "docker-compose.yml.previous").unlink(missing_ok=True)
    PREVIOUS_FILE.unlink(missing_ok=True)


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
    tag_images()
    if (workdir / "os.tar").is_file():
        job.step("packages", 20, "Installing the operating system updates ...")
        with tarfile.open(workdir / "os.tar") as tar:
            tar.extractall(workdir / "os", filter="data")
        run(str(PROJECT_DIR / "install" / "install.sh"), "--only=packages", f"--offline={workdir / 'os'}", "--yes")

    job.step("images", 35, "Loading the new images ...")
    run("docker", "load", "-i", str(workdir / "images.tar"))
    journal("loaded")
    job.step("save", 45, "Saving the running version ...")
    save(job)

    # Whatever happens from here on, the old compose file, install/ and database have to come back.
    journal("switched")
    migrate_back(job)
    added = add_env_defaults(manifest.get("env_defaults"))
    if added:
        job.say(f"New settings with their defaults: {', '.join(added)}")
    if (workdir / "install.tar").is_file():
        job.say("Updating install/ ...")
        with tarfile.open(workdir / "install.tar") as tar:
            tar.extractall(workdir / "new", filter="fully_trusted")  # signed - and needs its executable bits
    os.sync()  # the unpacked install/ on the card before it takes the old one's place
    write_file(PROJECT_DIR / "docker-compose.yml", (workdir / "docker-compose.yml").read_bytes())
    if (workdir / "new" / "install").is_dir():
        replace_dir(workdir / "new" / "install", PROJECT_DIR / "install")

    job.step("restart", 60, "Starting the new version ...")
    compose("up", "-d", "--remove-orphans")
    job.step("verify", 80, "Waiting for the new version to answer ...")
    if not healthy():
        raise UpdateError("The new version did not answer within "
                          f"{HEALTH_TIMEOUT} seconds.")
    if (workdir / "install.tar").is_file():
        job.step("services", 90, "Updating the device's services ...")
        run(str(PROJECT_DIR / "install" / "install.sh"), "--only=hardware,services,kiosk", "--yes")


def accept(bundle, options_file, attempt):
    """The checked bundle from incoming/ to active/, where nothing takes it up again by itself -
    the journal first, so that a power cut in between still finds it."""
    journal("accepted", fresh=True, bundle=bundle.name, attempt=attempt, retry=True, started=time.time())
    ACTIVE.mkdir(parents=True, exist_ok=True)
    moved = ACTIVE / bundle.name, ACTIVE / options_file.name
    os.replace(bundle, moved[0])
    if options_file.exists():
        os.replace(options_file, moved[1])
    sync_dir(ACTIVE)
    return moved


def process_bundle(bundle):
    name = bundle.name
    if other_maintenance():
        log(f"{name}: waiting - the device is busy with: {other_maintenance()}")
        return False
    options_file = bundle.with_name(bundle.name + ".options.json")
    options = read_json(options_file, {}) or {}
    attempt = int(options.get("attempt", 1))
    job = Job(name, fresh=attempt == 1)
    job.step("starting", 5, f"Checking {name} ..." + (f" (attempt {attempt} of {MAX_ATTEMPTS})" if attempt > 1 else ""))
    event("update.start", name, details=f"attempt {attempt}" if attempt > 1 else "",
          actor=options.get("requested_by", "update watcher"))
    workdir = Path(tempfile.mkdtemp(dir=UPDATES, prefix=".work-"))
    accepted = False
    try:
        manifest = read_manifest(bundle, workdir)
        check_bundle(manifest, bundle)
        job.say(f"Bundle {manifest.get('version')} for {manifest.get('platform')}: signature and checks passed")
        job.step("unpack", 15, "Unpacking the update ...")
        unpack(bundle, manifest, workdir)
        bundle, options_file = accept(bundle, options_file, attempt)
        accepted = True
        apply(job, manifest, workdir)
        write_json(INSTALLED_FILE, {"version": manifest.get("version"), "built_at": manifest.get("built_at"),
                                    "applied_at": time.time()})
        JOURNAL_FILE.unlink()
        forget_previous()
        run("docker", "image", "prune", "-f", check=False)  # the old version's images, untagged just now
        job.finish("done", f"Update to {manifest.get('version')} applied.", version=manifest.get("version"))
        event("update.apply", name, details=str(manifest.get("version")))
        restart_self = (workdir / "install.tar").is_file()
    except Exception as exc:  # noqa: BLE001 - every failure ends in the log and the dashboard
        job.say(f"FAILED: {exc}")
        restart_self = False
        if accepted:
            journal(read_json(JOURNAL_FILE, {}).get("phase", "accepted"), retry=False)  # it would fail again
            try:
                undo(job, str(exc))
                JOURNAL_FILE.unlink()
                job.finish("rolled_back", f"Update failed and was rolled back: {exc}")
                event("update.apply", name, details=f"rolled back: {exc}", outcome="failure")
            except Exception as again:  # noqa: BLE001
                # The journal stays: the next start (or boot) tries again to reach the old version.
                job.finish("failed", f"Update failed, and so did the rollback: {again}")
                event("update.rollback", name, details=str(again), outcome="failure")
        else:
            job.finish("failed", f"Update refused: {exc}")
            event("update.apply", name, details=str(exc), outcome="failure")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        # Gone once it is over either way: applied, it is no longer needed (the rollback has its
        # own copies); refused or rolled back, it would only fail again. Only an update cut short
        # - the journal still there - keeps it, for recover(). active/ goes with it: after a step
        # back to a release, that release's watcher knows nothing of it.
        if not JOURNAL_FILE.exists():
            bundle.unlink(missing_ok=True)
            options_file.unlink(missing_ok=True)
            shutil.rmtree(ACTIVE, ignore_errors=True)
    if restart_self:
        # install/ brought a new version of this very script: hand over to it.
        run("systemctl", "restart", "--no-block", "coffeepy-update-watcher.service", check=False)
    return True


def recover():
    """An update that a power cut (or a killed watcher) interrupted: back to the version before
    it, then - unless it had failed on its own, or too often - its bundle back into incoming/,
    so that it runs again from the start. Returns whether there was one."""
    for work in UPDATES.glob(".work-*"):
        shutil.rmtree(work, ignore_errors=True)
    data = read_json(JOURNAL_FILE)
    if not data:
        # Left over between the journal's end and its bundle's - or the copies an older watcher
        # kept for a rollback by hand, at its successor's first start.
        shutil.rmtree(ACTIVE, ignore_errors=True)
        forget_previous()
        return False
    name, attempt = data.get("bundle", "?"), int(data.get("attempt", 1))
    job = Job(name, fresh=False)
    job.step("recover", 10, f"The update was interrupted (at {data.get('phase')}, attempt {attempt}) - "
                            "going back to the version before it ...")
    try:
        undo(job, "the update was interrupted")
    except Exception as exc:  # noqa: BLE001 - the journal stays for the next start
        job.finish("failed", f"The interrupted update could not be undone: {exc} - "
                             "it is tried again when the device restarts.")
        event("update.recover", name, details=str(exc), outcome="failure")
        return True
    # Cut short before accept() moved it, the bundle is still in incoming/ - and so are its options.
    bundle = next((p for p in (ACTIVE / name, INCOMING / name) if p.exists()), None)
    if data.get("retry") and attempt < MAX_ATTEMPTS and bundle is not None:
        options = read_json(bundle.with_name(f"{name}.options.json"), {}) or {}
        write_json(INCOMING / f"{name}.options.json", {**options, "attempt": attempt + 1})
        os.replace(bundle, INCOMING / name)
        sync_dir(INCOMING)
        message = f"The interrupted update was undone - it runs again (attempt {attempt + 1} of {MAX_ATTEMPTS})."
        outcome = "success"
    else:
        if bundle is not None:  # in incoming/ it would start over as a first attempt
            bundle.unlink()
            bundle.with_name(f"{name}.options.json").unlink(missing_ok=True)
        message = "The interrupted update was undone - the device stays on the version before it."
        outcome = "failure"
    JOURNAL_FILE.unlink()
    shutil.rmtree(ACTIVE, ignore_errors=True)
    job.finish("rolled_back", message)
    event("update.recover", name, details=f"interrupted at {data.get('phase')}, attempt {attempt}", outcome=outcome)
    return True


def cleanup_uploads():
    """Leftovers of uploads that never finished (a timeout, the WiFi gone), and a rollback an
    older dashboard asked for - there is no going back by hand any more."""
    for path in INCOMING.glob(".uploading-*"):
        if time.time() - path.stat().st_mtime > STALE_UPLOAD_SECONDS:
            path.unlink(missing_ok=True)
    (INCOMING / ROLLBACK_REQUEST).unlink(missing_ok=True)


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
    if JOURNAL_FILE.exists():
        # An update whose rollback failed: nothing new until recover() got the device back -
        # it runs when the watcher (or the device) starts again.
        return last_system
    changed = False
    for bundle in sorted(INCOMING.glob("*.tar")) + sorted(INCOMING.glob("*.tar.gz")):
        process_bundle(bundle)
        changed = True
    if changed or time.time() - last_system >= SYSTEM_SECONDS:
        write_system_info()
        return time.time()
    return last_system


def main(once=False, recover_only=False):
    for directory in (INCOMING, STATUS_DIR, PRE_UPDATE):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        # At boot coffeepy-update-recover.service got here first; this one is for a watcher
        # that died mid-update without a reboot (systemd restarts it).
        recover()
    except Exception as exc:  # noqa: BLE001 - keep watching
        log(f"recovery failed: {exc}")
    if recover_only:
        return 0
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
    sys.exit(main(recover_only="--recover" in sys.argv[1:]))
