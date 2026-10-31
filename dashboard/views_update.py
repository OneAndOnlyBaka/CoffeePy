"""The update page: the installed version, uploading a bundle, what the host's update
watcher (install/update_watcher.py) is doing with it. There is no going back by hand: a
version that came up is fixed by the next update, never by the one before. Admins only. The dashboard only drops files into data/updates/incoming/ - all the
work, and every check, is the watcher's."""
import json
import os
import time
import uuid
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from . import audit, import_jobs, restore_jobs, versions
from .decorators import superuser_required

# The watcher rewrites state.json at every step; a running job that stops moving is dead.
STALE_SECONDS = 1800


def _read(name, default=None):
    try:
        return json.loads((settings.UPDATE_DIR / name).read_text())
    except (OSError, ValueError):
        return default


def watcher_state() -> dict:
    state = _read("state.json", {}) or {}
    if state.get("state") == "running" and time.time() - state.get("updated", 0) > STALE_SECONDS:
        state["state"] = "stale"
    return state


def _busy() -> str | None:
    # journal.json: an update that is under way - or was cut short and waits for the next start.
    if watcher_state().get("state") == "running" or _pending() or (settings.UPDATE_DIR / "journal.json").exists():
        return _("An update is already being applied.")
    if restore_jobs.is_running() or import_jobs.is_running() or restore_jobs.maintenance_active() == "restore":
        return _("A restore or an import is running - try again when it is done.")
    return None


def _pending() -> bool:
    incoming = settings.UPDATE_INCOMING_DIR
    return incoming.is_dir() and any(p.suffix in (".tar", ".gz") for p in incoming.iterdir()
                                     if not p.name.startswith("."))


@login_required
@superuser_required
def update(request):
    saved = settings.UPDATE_DIR / "pre-update"
    pre_update = sorted([*saved.glob("*.sql.gz"), *saved.glob("*.dump")],  # .dump: an older watcher's
                        key=lambda p: p.stat().st_mtime, reverse=True) if saved.is_dir() else []
    installed = _read("installed.json", {}) or {}
    if installed.get("applied_at"):
        installed["applied"] = datetime.fromtimestamp(installed["applied_at"], tz=timezone.get_current_timezone())
    system = _read("system.json")
    version_sections = versions.sections(installed, system if isinstance(system, dict) else None)
    return render(request, "dashboard/update.html", {
        "version": settings.COFFEEPY_VERSION,
        "version_sections": version_sections,
        "version_text": versions.as_text(version_sections),
        # The device installs anything uploaded here - fine on a test device, never in use.
        "signature_off": isinstance(system, dict) and not (system.get("signature") or {}).get("check", True),
        "system_missing": not isinstance(system, dict),
        "installed": installed,
        "state": watcher_state(),
        "pending": _pending(),
        "pre_update": [{"name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime} for p in pre_update],
        "os_packages": (settings.UPDATE_DIR / "os-packages.txt").is_file(),
    })


@login_required
@superuser_required
@require_GET
def update_packages(request):
    """The host's installed packages, as the watcher lists them - what scripts/build_os_bundle.sh
    --packages starts from. Here because SSH is off on a device in use."""
    path = settings.UPDATE_DIR / "os-packages.txt"
    if not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename="coffeepy-os-packages.txt",
                        content_type="text/plain")


@login_required
@superuser_required
@require_POST
def update_upload(request):
    upload = request.FILES.get("bundle")
    busy = _busy()
    if busy:
        messages.error(request, busy)
    elif not upload or not upload.name.endswith((".tar", ".tar.gz")):
        messages.error(request, _("Please upload a .tar update bundle."))
    else:
        incoming = settings.UPDATE_INCOMING_DIR
        incoming.mkdir(parents=True, exist_ok=True)
        name = os.path.basename(upload.name)
        # The options first: the watcher may pick the bundle up the moment it appears.
        (incoming / f"{name}.options.json").write_text(json.dumps({"requested_by": request.user.get_username()}))
        tmp = incoming / f".uploading-{uuid.uuid4().hex}"
        with open(tmp, "wb") as f:
            for chunk in upload.chunks():
                f.write(chunk)
        os.replace(tmp, incoming / name)  # atomic: the watcher never sees a partial file
        audit.log(request, "update.upload", target=name)
        messages.success(request, _("Uploaded %(name)s - it is checked and applied now.") % {"name": name})
    return redirect("dashboard:update")


@login_required
@superuser_required
@require_GET
def update_status(request):
    status_dir = settings.UPDATE_STATUS_DIR
    log = ""
    if status_dir.is_dir():
        logs = sorted(status_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        log = logs[0].read_text(errors="replace") if logs else ""
    return JsonResponse({"state": watcher_state(), "log": log, "pending": _pending()})
