"""Database backups: on the USB stick the host's storage helper looks after
(install/storage_helper.py), and back from it.

The stick is only ever written when its metadata says it is *this* device's current backup
stick - the host mounts nothing else at settings.USB_DIR/backup, but the mount point is a
plain directory on the SD card while no stick is in, and writing there would quietly fill
the card instead. So every write checks the metadata first.

A backup is `pg_dump --format=custom` (compressed, restorable with pg_restore). The older
plain-SQL dumps (coffee_backup_N.sql) restore too, through psql.
"""
import json
import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_noop

META = ".coffeepy-backup.json"
SUFFIXES = (".dump", ".sql")
# The helper rewrites status.json every few seconds; older than this, it isn't running.
HELPER_STALE_SECONDS = 20


class BackupError(Exception):
    """Something to show the admin as it is. The messages of our own are msgids
    (gettext_noop), translated where they are shown; pg_dump's own stay as they are."""


# --- the stick, as the host helper reports it ---------------------------------------------

def storage_status() -> dict:
    try:
        status = json.loads((settings.STORAGE_DIR / "status.json").read_text())
    except (OSError, ValueError):
        return {"helper_running": False}
    status["helper_running"] = time.time() - status.get("updated", 0) < HELPER_STALE_SECONDS
    return status


def device_id() -> str:
    try:
        return (settings.STORAGE_DIR / "device-id").read_text().strip()
    except OSError:
        return ""


def configured_stick() -> dict | None:
    try:
        return json.loads((settings.STORAGE_DIR / "stick.json").read_text())
    except (OSError, ValueError):
        return None


def read_meta(root: Path) -> dict | None:
    try:
        return json.loads((root / META).read_text())
    except (OSError, ValueError):
        return None


def own_root() -> Path:
    return settings.USB_DIR / "backup"


def foreign_root() -> Path:
    return settings.USB_DIR / "foreign"


def own_backup_dir() -> Path | None:
    """Where backups go now, or None: only the stick stick.json names, mounted."""
    meta, stick = read_meta(own_root()), configured_stick()
    if not meta or not stick or meta.get("device_id") != device_id() or meta.get("stick_id") != stick.get("stick_id"):
        return None
    path = own_root() / "backups"
    path.mkdir(exist_ok=True)
    return path


def foreign_backup_dir() -> Path | None:
    return foreign_root() / "backups" if read_meta(foreign_root()) else None


def list_backups(directory: Path | None) -> list[dict]:
    if directory is None or not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix in SUFFIXES]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{
        "name": p.name,
        "size": p.stat().st_size,
        "created": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.get_current_timezone()),
    } for p in files]


def resolve(directory: Path | None, name: str) -> Path:
    """A backup file in `directory` by name - never a path that leads out of it."""
    if directory is None:
        raise BackupError(gettext_noop("No backup stick is plugged in."))
    target = (directory / name).resolve()
    if target.parent != directory.resolve() or not target.is_file() or target.suffix not in SUFFIXES:
        raise BackupError(gettext_noop("No such backup."))
    return target


# --- what the last automatic backup did -------------------------------------------------

def read_backup_status() -> dict:
    try:
        return json.loads((settings.STORAGE_DIR / "backup-status.json").read_text())
    except (OSError, ValueError):
        return {}


def write_backup_status(**fields) -> None:
    status = {**read_backup_status(), **fields, "attempted": time.time()}
    path = settings.STORAGE_DIR / "backup-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    os.replace(tmp, path)


def health() -> dict:
    """For the dashboard's overview: are backups actually being made right now?
    `state` is ok | missing (a stick is set up but not plugged in) | none (never set up) |
    failing (plugged in, but the last backup failed) | helper (the host service is down)."""
    status = read_backup_status()
    last_ok = status.get("last_ok")
    info = {"last_ok": datetime.fromtimestamp(last_ok, tz=timezone.get_current_timezone()) if last_ok else None,
            "error": status.get("error", ""), "configured": configured_stick()}
    if not storage_status().get("helper_running"):
        return {**info, "state": "helper"}
    if configured_stick() is None:
        return {**info, "state": "none"}
    if own_backup_dir() is None:
        return {**info, "state": "missing"}
    if status.get("error"):
        return {**info, "state": "failing"}
    return {**info, "state": "ok"}


# --- pg_dump / pg_restore -----------------------------------------------------------------

def _pg():
    db = settings.DATABASES["default"]
    args = ["--host", db["HOST"] or "localhost", "--port", str(db["PORT"] or "5432"), "--username", db["USER"],
            "--no-password"]
    # Warnings and errors only: a restore's "drop cascades to table ..." notices would bury
    # the one line that matters when it fails.
    env = {**os.environ, "PGPASSWORD": db["PASSWORD"] or "", "PGOPTIONS": "-c client_min_messages=warning"}
    return args, env, db["NAME"]


def _run(cmd, env):
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"{cmd[0]}: {(result.stderr or result.stdout).strip()[-500:]}")
    return result.stdout


def dump(target: Path) -> Path:
    """The whole database into `target` - written under a temporary name and renamed, so a
    stick pulled half-way never leaves a broken file that looks like a backup."""
    args, env, name = _pg()
    tmp = target.with_name(f".{target.name}.part")
    try:
        _run(["pg_dump", *args, "--format", "custom", "--file", str(tmp), name], env)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def new_backup(directory: Path, keep: int | None = None) -> Path:
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M%S")
    target = dump(directory / f"coffeepy_{stamp}.dump")
    if keep:
        rotate(directory, keep)
    return target


def rotate(directory: Path, keep: int) -> None:
    """Keeps the newest `keep` automatic backups; a file put there by hand is left alone."""
    ours = sorted((p for p in directory.glob("coffeepy_*.dump")), key=lambda p: p.name, reverse=True)
    for old in ours[keep:]:
        old.unlink(missing_ok=True)


def check_restorable(path: Path) -> str:
    """'custom' or 'plain' - or BackupError for anything that isn't a PostgreSQL backup
    (an old CoffeePy SQLite database belongs on the import page instead)."""
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith(b"PGDMP"):
        args, env, _ = _pg()
        _run(["pg_restore", "--list", str(path)], env)
        return "custom"
    if b"PostgreSQL database dump" in head:
        return "plain"
    if head.startswith(b"SQLite format 3") or b"CREATE TABLE" in head:
        raise BackupError(gettext_noop("This is an old CoffeePy database, not a backup - use \"Import legacy DB\" for it."))
    raise BackupError(gettext_noop("This file is not a CoffeePy backup."))


# pg_dump/pg_restore in the image are newer than the database server (17 against 16), and a
# 17 dump starts with a setting 16 doesn't know - fatal to the restore, nothing else in it is.
UNKNOWN_TO_SERVER = (b"SET transaction_timeout",)


def restore(path: Path) -> None:
    """Replaces the whole database with the backup at `path` - all or nothing: dropping the
    old tables and loading the backup's run as one transaction (psql --single-transaction),
    so a backup that fails half-way is rolled back and leaves the data as it was. Every other
    connection is cut first (the kiosk's polls, the MQTT listener - they reconnect on their
    own) so none of them holds a lock the drop would wait for."""
    kind = check_restorable(path)
    args, env, name = _pg()
    _run(["psql", *args, "--dbname", name, "--quiet", "-c",
          "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() "
          "AND pid <> pg_backend_pid();"], env)
    if kind == "custom":
        # As SQL rather than straight into the database, so it can go through the one psql
        # transaction - and past the filter.
        source = subprocess.Popen(["pg_restore", "--no-owner", "--no-privileges", "--file", "-", str(path)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = source.stdout
    else:
        source = None
        lines = open(path, "rb")
    psql = subprocess.Popen(["psql", *args, "--dbname", name, "--single-transaction", "-v", "ON_ERROR_STOP=1",
                             "--quiet", "--file", "-"],
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        psql.stdin.write(b"DROP SCHEMA public CASCADE;\nCREATE SCHEMA public;\n")
        for line in lines:
            if not line.startswith(UNKNOWN_TO_SERVER):
                psql.stdin.write(line)
        psql.stdin.close()
    except BrokenPipeError:
        pass  # psql stopped at an error - reported below
    finally:
        lines.close()
    errors = psql.stderr.read().decode(errors="replace")
    if psql.wait() != 0:
        if source:
            source.kill()
        raise BackupError(f"psql: {errors.strip()[-500:]}")
    if source and source.wait() != 0:
        raise BackupError(f"pg_restore: {source.stderr.read().decode(errors='replace').strip()[-500:]}")


def storage_request(action: str, **fields) -> str:
    """Asks the host's storage helper to do something to a stick (format, adopt, eject);
    its outcome appears in storage_status()["request"] under the returned id."""
    request_id = uuid.uuid4().hex
    directory = settings.STORAGE_DIR / "requests"
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{request_id}.tmp"
    tmp.write_text(json.dumps({"id": request_id, "action": action, **fields}))
    os.replace(tmp, directory / f"{request_id}.json")
    return request_id
