"""Backups of the whole device: onto the USB stick the host's storage helper looks after
(install/storage_helper.py), and back from it.

The stick is only ever written when its metadata says it is *this* device's current backup
stick - the host mounts nothing else at settings.USB_DIR/backup, but the mount point is a
plain directory on the SD card while no stick is in, and writing there would quietly fill
the card instead. So every write checks the metadata first.

A backup is one file, coffeepy_<time>.backup: a tar with
  manifest.json      when, which app version, which database migrations, which device
  database.dump      pg_dump --format=custom (compressed)
  config/            what a replacement device needs besides the data: .env, the device
                     CA and certificates (data/tls), the device id, which stick it backs up to
With BACKUP_PASSPHRASE set (.env, asked by scripts/provision.sh) the tar is encrypted -
AES-256 with a PBKDF2 key, through openssl, which the image and the host both have - and
named .backup.enc. Without one it is not, on purpose: the stick sits inside the housing.
Every backup has a .sha256 next to it, and is read back once right after it is written.

Older backups restore too: the plain database dumps of earlier versions (.dump, .sql).
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_noop

META = ".coffeepy-backup.json"
ARCHIVE_SUFFIXES = (".backup", ".backup.enc")
LEGACY_SUFFIXES = (".dump", ".sql")
FORMAT = 1
# The helper rewrites status.json every few seconds; older than this, it isn't running.
HELPER_STALE_SECONDS = 20
# Copies taken right before a restore: a safety net, not a history.
KEEP_BEFORE_RESTORE = 3
PBKDF2_ITERATIONS = 600000
# The settings a backup brings back onto a device (the rest of .env - database password,
# secret key, admin password - belongs to the installation it runs on).
RESTORABLE_SETTINGS = (
    "DJANGO_LANGUAGE_CODE", "PUBLIC_HOST", "TLS_EXTRA_HOSTNAMES", "TIME_ZONE", "AUDIT_RETENTION_DAYS",
    "GUNICORN_WORKERS", "DJANGO_SECURE_COOKIES",
)


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


def storage_request(action: str, **fields) -> str:
    """Asks the host's storage helper to do something (format, adopt, eject a stick, apply
    settings); its outcome appears in storage_status()["request"] under the returned id."""
    request_id = uuid.uuid4().hex
    directory = settings.STORAGE_DIR / "requests"
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{request_id}.tmp"
    tmp.write_text(json.dumps({"id": request_id, "action": action, **fields}))
    os.replace(tmp, directory / f"{request_id}.json")
    return request_id


# --- the backup files on a stick ------------------------------------------------------------

def is_backup_name(name: str) -> bool:
    return name.endswith(ARCHIVE_SUFFIXES + LEGACY_SUFFIXES) and not name.startswith(".")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def write_checksum(path: Path) -> None:
    checksum_path(path).write_text(f"{sha256_of(path)}  {path.name}\n")


def verify(path: Path) -> str:
    """ok | damaged (the file no longer matches its checksum) | unknown (no checksum - a
    backup from before checksums, or one put there by hand)."""
    try:
        expected = checksum_path(path).read_text().split()[0]
    except (OSError, IndexError):
        return "unknown"
    return "ok" if expected == sha256_of(path) else "damaged"


def list_backups(directory: Path | None) -> list[dict]:
    if directory is None or not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and is_backup_name(p.name)]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{
        "name": p.name,
        "size": p.stat().st_size,
        "created": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.get_current_timezone()),
        "encrypted": p.name.endswith(".enc"),
        "device": p.name.endswith(ARCHIVE_SUFFIXES),
        "check": verify(p),
    } for p in files]


def resolve(directory: Path | None, name: str) -> Path:
    """A backup file in `directory` by name - never a path that leads out of it."""
    if directory is None:
        raise BackupError(gettext_noop("No backup stick is plugged in."))
    target = (directory / name).resolve()
    if target.parent != directory.resolve() or not target.is_file() or not is_backup_name(target.name):
        raise BackupError(gettext_noop("No such backup."))
    return target


# --- generations ------------------------------------------------------------------------------

STAMP = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{6})")


def _stamp(name: str) -> datetime | None:
    match = STAMP.search(name)
    return datetime.strptime(f"{match[1]} {match[2]}", "%Y-%m-%d %H%M%S") if match else None


def _dated(directory: Path, pattern: str) -> list:
    """(time, file) of the backups matching `pattern` whose name carries a time, newest first."""
    found = [(stamp, p) for p in directory.glob(pattern) if is_backup_name(p.name) and (stamp := _stamp(p.name))]
    return sorted(found, reverse=True)


def rotate(directory: Path, keep) -> list[str]:
    """Keeps, of the automatic backups: the newest `keep.keep_recent`, and the newest of each
    of the last `keep_daily` days, `keep_weekly` weeks and `keep_monthly` months. Of the
    copies taken before restores, the newest KEEP_BEFORE_RESTORE. Anything else on the stick
    (a file put there by hand) is left alone. Returns the names it deleted."""
    automatic = _dated(directory, "coffeepy_*")
    kept = {p for _, p in automatic[:keep.keep_recent]}
    for period, count in ((lambda t: t.date(), keep.keep_daily),
                          (lambda t: t.isocalendar()[:2], keep.keep_weekly),
                          (lambda t: (t.year, t.month), keep.keep_monthly)):
        seen = set()
        for stamp, path in automatic:
            if period(stamp) not in seen and len(seen) < count:
                seen.add(period(stamp))
                kept.add(path)
    deleted = [p for _, p in automatic if p not in kept]
    deleted += [p for _, p in _dated(directory, "before-restore_*")[KEEP_BEFORE_RESTORE:]]
    for path in deleted:
        path.unlink(missing_ok=True)
        checksum_path(path).unlink(missing_ok=True)
    return [p.name for p in deleted]


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
    """Are backups actually being made right now? `state` is
      ok        the stick is in and the last backup is recent
      missing   a stick is set up but not plugged in
      failing   plugged in, but the last backup failed
      stale     plugged in, but no backup for much longer than the interval
      helper    the host's storage service is down
      none      no stick was ever set up
    `disturbed` is what the kiosk's warning light goes by: everything but ok and none."""
    from coffee.models import BackupSettings

    status = read_backup_status()
    last_ok = status.get("last_ok")
    info = {"last_ok": datetime.fromtimestamp(last_ok, tz=timezone.get_current_timezone()) if last_ok else None,
            "error": status.get("error", ""), "configured": configured_stick(), "encrypted": bool(passphrase())}
    if not storage_status().get("helper_running"):
        state = "helper"
    elif info["configured"] is None:
        state = "none"
    elif own_backup_dir() is None:
        state = "missing"
    elif status.get("error"):
        state = "failing"
    elif last_ok and time.time() - last_ok > max(3 * BackupSettings.current().interval_minutes * 60, 3 * 3600):
        state = "stale"
    else:
        state = "ok"
    return {**info, "state": state, "disturbed": state not in ("ok", "none")}


# --- pg_dump / pg_restore -----------------------------------------------------------------

def _pg():
    db = settings.DATABASES["default"]
    args = ["--host", db["HOST"] or "localhost", "--port", str(db["PORT"] or "5432"), "--username", db["USER"],
            "--no-password"]
    # Warnings and errors only: a restore's "drop cascades to table ..." notices would bury
    # the one line that matters when it fails.
    env = {**os.environ, "PGPASSWORD": db["PASSWORD"] or "", "PGOPTIONS": "-c client_min_messages=warning"}
    return args, env, db["NAME"]


def _run(cmd, env=None):
    result = subprocess.run(cmd, env=env, capture_output=True)
    if result.returncode != 0:
        raise BackupError(f"{cmd[0]}: {(result.stderr or result.stdout).decode(errors='replace').strip()[-500:]}")
    return result.stdout.decode(errors="replace")


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


# --- encryption ---------------------------------------------------------------------------

def passphrase() -> str:
    return os.environ.get("BACKUP_PASSPHRASE", "")


def _openssl(decrypt: bool, src: Path, dst: Path, secret: str) -> None:
    """AES-256 with a key derived from the passphrase (PBKDF2, salted). The passphrase goes
    in through the environment, not the command line, where `ps` would show it."""
    cmd = ["openssl", "enc", "-d" if decrypt else "-salt", "-aes-256-cbc", "-pbkdf2", "-iter", str(PBKDF2_ITERATIONS),
           "-md", "sha256", "-in", str(src), "-out", str(dst), "-pass", "env:COFFEEPY_BACKUP_SECRET"]
    try:
        _run(cmd, env={**os.environ, "COFFEEPY_BACKUP_SECRET": secret})
    except BackupError:
        dst.unlink(missing_ok=True)
        if decrypt:
            raise BackupError(gettext_noop("The backup could not be decrypted - wrong passphrase?")) from None
        raise


def encrypt(src: Path, dst: Path, secret: str) -> None:
    _openssl(False, src, dst, secret)


def decrypt(src: Path, dst: Path, secret: str) -> None:
    _openssl(True, src, dst, secret)


# --- making a backup ----------------------------------------------------------------------

def applied_migrations() -> list[str]:
    from django.db.migrations.recorder import MigrationRecorder

    return sorted(f"{app}.{name}" for app, name in MigrationRecorder.Migration.objects.values_list("app", "name"))


def config_files() -> list[tuple[str, Path]]:
    """What a replacement device needs besides the database, as (name in the archive, file)."""
    files = []
    if settings.COFFEEPY_ENV_FILE.is_file():
        files.append(("config/env", settings.COFFEEPY_ENV_FILE))
    if settings.TLS_DIR.is_dir():
        files += [(f"config/tls/{p.name}", p) for p in sorted(settings.TLS_DIR.iterdir())
                  if p.is_file() and not p.name.startswith(".")]  # not .lock and the like
    for name in ("device-id", "stick.json"):
        if (settings.STORAGE_DIR / name).is_file():
            files.append((f"config/{name}", settings.STORAGE_DIR / name))
    return files


def create_backup(directory: Path, prefix: str = "coffeepy") -> Path:
    """One device backup into `directory`, checked: its checksum written, and the file read
    back - decrypted and the database's table of contents listed - before it counts."""
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M%S")
    secret = passphrase()
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".backup" + (".enc" if secret else "")
    target = directory / f"{prefix}_{stamp}{suffix}"
    number = 1
    while target.exists():  # two in the same second ("Back up now" twice): never overwrite one
        number += 1
        target = directory / f"{prefix}_{stamp}-{number}{suffix}"
    with tempfile.TemporaryDirectory(dir=directory, prefix=".work-") as work:
        work = Path(work)
        dump(work / "database.dump")
        files = config_files()
        manifest = {
            "format": FORMAT,
            "created": timezone.now().isoformat(),
            "app_version": settings.COFFEEPY_VERSION,
            "device_id": device_id(),
            "encrypted": bool(secret),
            "migrations": applied_migrations(),
            "contents": ["database.dump"] + [arcname for arcname, _ in files],
        }
        archive = work / "archive.tar"
        with tarfile.open(archive, "w") as tar:
            data = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime = len(data), int(time.time())
            tar.addfile(info, io.BytesIO(data))
            tar.add(work / "database.dump", "database.dump")
            for arcname, path in files:
                tar.add(path, arcname)
        if secret:
            encrypt(archive, work / "archive.enc", secret)
            archive = work / "archive.enc"
        os.replace(archive, work / target.name)
        os.replace(work / target.name, target)  # same file system: a stick pulled now leaves no half file
    write_checksum(target)
    try:
        with open_backup(target) as backup:  # read back what was written, or it isn't a backup
            backup.check_database()
    except BackupError:
        target.unlink(missing_ok=True)
        checksum_path(target).unlink(missing_ok=True)
        raise
    return target


def new_backup(directory: Path) -> Path:
    from coffee.models import BackupSettings

    target = create_backup(directory)
    rotate(directory, BackupSettings.current())
    return target


# --- reading a backup -----------------------------------------------------------------------

@dataclass
class Backup:
    """An opened backup: its database dump and, for a device backup, its manifest and
    config files - in a temporary directory that goes away with the `with` block."""

    database: Path
    kind: str  # "custom" (pg_restore) or "plain" (psql)
    manifest: dict | None = None
    config: Path | None = None
    files: list = field(default_factory=list)

    def check_database(self) -> None:
        if self.kind == "custom":
            _, env, _ = _pg()
            _run(["pg_restore", "--list", str(self.database)], env)

    def migrations(self) -> list[str]:
        """The database migrations the backup's data has - from the manifest, or read out
        of an older dump's django_migrations table."""
        if self.manifest is not None:
            return self.manifest.get("migrations", [])
        if self.kind == "custom":
            _, env, _ = _pg()
            text = _run(["pg_restore", "--data-only", "--table", "django_migrations", "--file", "-",
                         str(self.database)], env)
        else:
            text = self.database.read_text(errors="replace")
        return parse_migrations(text)

    def config_file(self, name: str) -> Path | None:
        path = self.config / name if self.config else None
        return path if path is not None and path.is_file() else None


def parse_migrations(sql: str) -> list[str]:
    """`app.name` for each row of the django_migrations COPY block of a SQL dump."""
    rows, inside = [], False
    for line in sql.splitlines():
        if line.startswith("COPY ") and "django_migrations" in line:
            inside = True
        elif inside and line == "\\.":
            break
        elif inside:
            parts = line.split("\t")
            if len(parts) >= 3:
                rows.append(f"{parts[1]}.{parts[2]}")
    return sorted(rows)


def _kind_of(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith(b"PGDMP"):
        return "custom"
    if b"PostgreSQL database dump" in head:
        return "plain"
    if head.startswith(b"SQLite format 3") or b"CREATE TABLE" in head:
        raise BackupError(gettext_noop("This is an old CoffeePy database, not a backup - use \"Import legacy DB\" for it."))
    raise BackupError(gettext_noop("This file is not a CoffeePy backup."))


@contextmanager
def open_backup(path: Path, secret: str | None = None):
    """Opens any backup - a device backup (encrypted or not) or an older plain database
    dump - and yields a Backup. `secret` overrides BACKUP_PASSPHRASE: a backup from another
    device, or from before the passphrase was changed."""
    path = Path(path)
    with tempfile.TemporaryDirectory(dir=settings.STORAGE_DIR if settings.STORAGE_DIR.is_dir() else None) as work:
        work = Path(work)
        with open(path, "rb") as f:
            encrypted = f.read(8) == b"Salted__"
        if encrypted:
            secret = secret or passphrase()
            if not secret:
                raise BackupError(gettext_noop("This backup is encrypted - enter its passphrase."))
            decrypt(path, work / "archive.tar", secret)
            path = work / "archive.tar"
            # CBC's padding is all openssl tells a wrong passphrase by, and about one in 256 passes
            # it - what comes out is noise, never the tar an encrypted backup always is.
            if not tarfile.is_tarfile(path):
                raise BackupError(gettext_noop("The backup could not be decrypted - wrong passphrase?"))
        if tarfile.is_tarfile(path):
            with tarfile.open(path) as tar:
                names = tar.getnames()
                if "manifest.json" not in names or "database.dump" not in names:
                    raise BackupError(gettext_noop("This file is not a CoffeePy backup."))
                tar.extractall(work / "content", filter="data")
            content = work / "content"
            yield Backup(database=content / "database.dump", kind="custom",
                         manifest=json.loads((content / "manifest.json").read_text()),
                         config=content / "config" if (content / "config").is_dir() else None, files=names)
        else:
            yield Backup(database=path, kind=_kind_of(path))


def code_migrations() -> set[str]:
    from django.db.migrations.loader import MigrationLoader

    return {f"{app}.{name}" for app, name in MigrationLoader(None, ignore_no_migrations=True).disk_migrations}


def check_compatible(backup: Backup) -> None:
    """A backup from a newer version of CoffeePy has database changes this one doesn't know -
    restored, the app would trip over them. Older ones are brought up to date afterwards."""
    if set(backup.migrations()) - code_migrations():
        raise BackupError(gettext_noop("This backup comes from a newer version of CoffeePy - update this device "
                                       "first, then restore it."))


# --- restoring ------------------------------------------------------------------------------

# pg_dump/pg_restore in the image are newer than the database server (17 against 16), and a
# 17 dump starts with a setting 16 doesn't know - fatal to the restore, nothing else in it is.
UNKNOWN_TO_SERVER = (b"SET transaction_timeout",)


def restore_database(backup: Backup) -> None:
    """Replaces the whole database with the backup's - all or nothing: dropping the old
    tables and loading the backup's run in one transaction, which is only committed once
    the whole backup went in *and* pg_restore finished without an error. A backup that fails
    half-way - in psql, or a dump pg_restore can only read half of - is rolled back and
    leaves the data as it was. Every other connection is cut first (the kiosk's polls, the
    MQTT listener - they reconnect on their own) so none of them holds a lock the drop would
    wait for."""
    args, env, name = _pg()
    _run(["psql", *args, "--dbname", name, "--quiet", "-c",
          "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() "
          "AND pid <> pg_backend_pid();"], env)
    if backup.kind == "custom":
        # As SQL rather than straight into the database, so it can go through the one psql
        # transaction - and past the filter.
        source = subprocess.Popen(["pg_restore", "--no-owner", "--no-privileges", "--file", "-",
                                   str(backup.database)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = source.stdout
    else:
        source = None
        lines = open(backup.database, "rb")
    psql = subprocess.Popen(["psql", *args, "--dbname", name, "-v", "ON_ERROR_STOP=1", "--quiet", "--file", "-"],
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    source_error = ""
    try:
        psql.stdin.write(b"BEGIN;\nDROP SCHEMA public CASCADE;\nCREATE SCHEMA public;\n")
        for line in lines:
            if not line.startswith(UNKNOWN_TO_SERVER):
                psql.stdin.write(line)
        if source is not None and source.wait() != 0:
            source_error = source.stderr.read().decode(errors="replace").strip()[-500:]
        # A psql that ends without COMMIT ends its transaction rolled back.
        psql.stdin.write(b"ROLLBACK;\n" if source_error else b"COMMIT;\n")
        psql.stdin.close()
    except BrokenPipeError:
        pass  # psql stopped at an error - reported below
    finally:
        lines.close()
    errors = psql.stderr.read().decode(errors="replace")
    if source is not None and source.poll() is None:
        source.kill()
        source.wait()
    if psql.wait() != 0:
        raise BackupError(f"psql: {errors.strip()[-500:]}")
    if source_error:
        raise BackupError(f"pg_restore: {source_error}")


def restore_certificates(backup: Backup) -> int:
    """The device CA and certificates back into data/tls - phones that trusted the old
    device trust the restored one. Returns how many files were put back."""
    source = backup.config / "tls" if backup.config else None
    if source is None or not source.is_dir():
        return 0
    settings.TLS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in source.iterdir():
        target = settings.TLS_DIR / path.name
        shutil.copyfile(path, target)
        os.chmod(target, 0o600 if path.suffix == ".key" else 0o644)
        count += 1
    return count


def restore_identity(backup: Backup) -> bool:
    """The device id and its stick: the replacement device becomes the old one - and the old
    device's backup stick is its own backup stick again."""
    restored = False
    for name in ("device-id", "stick.json"):
        path = backup.config_file(name)
        if path is not None:
            settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, settings.STORAGE_DIR / name)
            restored = True
    return restored


def restorable_settings(backup: Backup) -> dict:
    """The settings of RESTORABLE_SETTINGS the backup's .env has."""
    path = backup.config_file("env")
    if path is None:
        return {}
    values = {}
    for line in path.read_text(errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in RESTORABLE_SETTINGS:
            values[key.strip()] = value.strip()
    return values
