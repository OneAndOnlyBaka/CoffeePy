"""Restores a database backup in the background, like import_jobs runs an import.

A restore replaces everything - members, bookings, and the dashboard logins too - so it is
wrapped: the kiosk shows its "back in a moment" screen meanwhile (settings.MAINTENANCE_DIR,
read by static/kiosk/maintenance.js), and the current database is saved first. The restore
itself is one transaction (coffee.backups.restore): a backup that fails to load is rolled
back and leaves the data as it was - the saved copy is for a backup that loads fine but
turns out to be the wrong one. The
progress lives in a file for the same reason as the import's: several gunicorn workers.
"""
import json
import os
import shutil
import threading
import time
from contextlib import suppress
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from coffee import backups

from . import audit

IDLE = {"state": "idle", "phase": "", "percent": 0, "message": "", "error": "", "updated": 0.0}
PHASES = [gettext_noop("Saving the current data"), gettext_noop("Restoring the backup"),
          gettext_noop("Updating the database"), gettext_noop("Done"), gettext_noop("Failed")]
STALE_AFTER_SECONDS = 1800


def read_status() -> dict:
    try:
        return {**IDLE, **json.loads(settings.RESTORE_STATUS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(IDLE)


def localized(status: dict) -> dict:
    status = dict(status)
    if status["phase"]:
        status["phase"] = _(status["phase"])
    if status["state"] == "error":
        status["message"] = _("Restore failed: %(error)s") % {"error": status["error"]}
    elif status["state"] == "done":
        status["message"] = _("Backup %(name)s restored. Log in again if the dashboard asks you to - "
                              "the logins are now the ones from the backup.") % {"name": status["message"]}
    return status


def write_status(**fields) -> None:
    path = settings.RESTORE_STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**IDLE, **fields, "updated": time.time()}))
    os.replace(tmp, path)


def is_running() -> bool:
    status = read_status()
    return status["state"] == "running" and time.time() - status["updated"] < STALE_AFTER_SECONDS


def _maintenance(step: str | None, percent: int = 0) -> None:
    """The kiosk's maintenance screen - the same file and format install/maintenance.sh writes."""
    path = settings.MAINTENANCE_DIR / "status.json"
    with suppress(OSError):
        if step is None:
            path.unlink(missing_ok=True)
            return
        settings.MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".status.json.tmp")
        tmp.write_text(json.dumps({"kind": "restore", "step": step, "percent": percent, "updated": int(time.time())}))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)


def start_restore(path: Path, name: str, actor: str, delete_after: bool = False) -> threading.Thread:
    """Restores the backup file at `path`; `delete_after` for an upload, which is only a
    temporary copy."""
    write_status(state="running", phase="Saving the current data", percent=5)
    thread = threading.Thread(target=_run, args=(Path(path), name, actor, delete_after), daemon=True)
    thread.start()
    return thread


def _safety_dir() -> Path:
    """Where the copy taken before a restore goes: the stick if it is in, else the SD card -
    only the newest one is kept there, it is a safety net, not a backup plan."""
    directory = backups.own_backup_dir()
    if directory is not None:
        return directory
    directory = settings.STORAGE_DIR / "before-restore"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _run(path: Path, name: str, actor: str, delete_after: bool) -> None:
    safety = None
    try:
        backups.check_restorable(path)
        _maintenance("restore_safety", 10)
        stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M%S")
        safety = backups.dump(_safety_dir() / f"before-restore_{stamp}.dump")

        write_status(state="running", phase="Restoring the backup", percent=35)
        _maintenance("restore", 35)
        connections.close_all()
        backups.restore(path)

        write_status(state="running", phase="Updating the database", percent=80)
        _maintenance("migrate", 80)
        # An older backup lacks the newest tables and columns.
        call_command("migrate", interactive=False, verbosity=0)
        audit.log_actor(actor, "backup.restore", target=name, details=f"safety copy: {safety.name}")
        write_status(state="done", phase="Done", percent=100, message=name)
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin, not swallowed
        write_status(state="error", phase="Failed", error=str(exc))
    finally:
        _maintenance(None)
        if delete_after:
            with suppress(OSError):
                path.unlink()
        connections.close_all()
