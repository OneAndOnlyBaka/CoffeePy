"""Runs the legacy-DB import in the background and keeps its progress readable.

Importing an old coffee.db can take a while (every order and payment is inserted row by
row), so the upload request only starts a worker thread and returns immediately; the
import page polls for progress. The state lives in a small JSON file
(settings.IMPORT_STATUS_FILE) rather than in process memory, because gunicorn serves the
dashboard from several workers and the poll may well land on a different one than the
upload did. It is transient by design: no file means "no import ran on this container".
"""

import io
import json
import os
import threading
import time
from contextlib import suppress

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from coffee.management.commands.import_sqlite import Command as ImportSqliteCommand

from . import audit

IDLE = {
    "state": "idle", "phase": "", "done": 0, "total": 0, "percent": 0, "message": "", "error": "", "updated": 0.0,
}

# The status file is written by a thread that has no request and so no language, and is read
# by pages in whatever language each poller chose - so it holds these English msgids and
# localized() translates them on the way out. The import command's own phases are listed
# here because the command (and its stdout, the "done" message) is deliberately left English.
PHASES = [
    gettext_noop("Starting"), gettext_noop("Reading source"), gettext_noop("Clearing existing data"),
    gettext_noop("Coffee sorts"), gettext_noop("Members"), gettext_noop("Orders"), gettext_noop("Payments"),
    gettext_noop("Done"), gettext_noop("Failed"),
]

# A job whose progress has not moved for this long is treated as gone (its worker was
# killed mid-import), so a stale status file can never block the next import forever.
STALE_AFTER_SECONDS = 600


def read_status() -> dict:
    try:
        with open(settings.IMPORT_STATUS_FILE, encoding="utf-8") as f:
            return {**IDLE, **json.load(f)}
    except (OSError, ValueError):
        return dict(IDLE)


def localized(status: dict) -> dict:
    """`status` as the import page shows it, in the active language."""
    status = dict(status)
    if status["phase"]:
        status["phase"] = _(status["phase"])
    if status["state"] == "error" and status["error"]:
        status["message"] = _("Import failed: %(error)s") % {"error": status["error"]}
    return status


def write_status(**fields) -> None:
    status = {**IDLE, **fields, "updated": time.time()}
    path = settings.IMPORT_STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f)
    os.replace(tmp, path)  # atomic, so a poll never reads a half-written file


def is_running() -> bool:
    status = read_status()
    if status["state"] != "running":
        return False
    return time.time() - status["updated"] < STALE_AFTER_SECONDS


def start_import(path: str, filename: str, actor: str, purge: bool = False) -> threading.Thread:
    """Starts the import of the uploaded file at `path` (which the job deletes when done).
    With `purge`, everything the import refills is deleted first - in the import's own
    transaction, so a failed import leaves the existing data untouched."""
    write_status(state="running", phase="Starting")
    thread = threading.Thread(target=_run, args=(path, filename, actor, purge), daemon=True)
    thread.start()
    return thread


def _run(path: str, filename: str, actor: str, purge: bool) -> None:
    last = None

    def progress(phase, done, total):
        nonlocal last
        percent = int(done * 100 / total) if total else 0
        if (phase, percent) == last:  # only write when the page would actually change
            return
        last = (phase, percent)
        write_status(state="running", phase=phase, done=done, total=total, percent=percent)

    try:
        command = ImportSqliteCommand()
        command.progress = progress
        out = io.StringIO()
        call_command(command, path=path, purge=purge, stdout=out)
        message = out.getvalue().strip()
        audit.log_actor(actor, "import.legacy_db", target=filename, details=message)
        write_status(state="done", phase="Done", percent=100, message=message)
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin, not swallowed
        write_status(state="error", phase="Failed", message=f"Import failed: {exc}", error=str(exc))
    finally:
        with suppress(OSError):
            os.unlink(path)
        connections.close_all()  # this thread owns its own DB connection
