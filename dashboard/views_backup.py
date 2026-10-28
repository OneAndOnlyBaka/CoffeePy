"""The backup page: the USB stick (setting one up, its backups), the backup settings, and
restoring - from a stick or from an uploaded file. Admins only. The work itself is in
coffee/backups.py, the stick's device side in install/storage_helper.py."""
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from coffee import backups
from coffee.models import BackupSettings

from . import audit, import_jobs, restore_jobs
from .forms import BackupSettingsForm, RestoreForm
from .decorators import superuser_required


def _busy() -> str | None:
    """Why a restore can't start now, or None."""
    if restore_jobs.is_running() or import_jobs.is_running():
        return _("A restore or an import is already running.")
    if restore_jobs.maintenance_active():
        return _("The device is being updated - try again when it is done.")
    return None


@login_required
@superuser_required
def backup(request):
    status = backups.storage_status()
    return render(request, "dashboard/backup.html", {
        "storage": status,
        "health": backups.health(),
        "own_backups": backups.list_backups(backups.own_backup_dir()),
        "foreign_backups": backups.list_backups(backups.foreign_backup_dir()),
        "foreign_is_ours": ((status.get("foreign") or {}).get("meta") or {}).get("device_id") == backups.device_id(),
        "settings_form": BackupSettingsForm(instance=BackupSettings.current()),
        "restore": restore_jobs.localized(restore_jobs.read_status()),
        "restore_running": restore_jobs.is_running(),
    })


@login_required
@superuser_required
@require_POST
def backup_now(request):
    try:
        call_command("backup_db", once=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin
        messages.error(request, _("Backup failed: %(error)s") % {"error": exc})
        audit.log(request, "backup.trigger", details=str(exc), outcome="failure")
    else:
        audit.log(request, "backup.trigger")
        messages.success(request, _("Backup completed."))
    return redirect("dashboard:backup")


@login_required
@superuser_required
@require_POST
def backup_settings(request):
    form = BackupSettingsForm(request.POST, instance=BackupSettings.current())
    if form.is_valid():
        form.save()
        audit.log(request, "backup.settings", details=", ".join(f"{k} {v}" for k, v in form.cleaned_data.items()))
        messages.success(request, _("Saved."))
    else:
        for field, errors in form.errors.items():
            messages.error(request, f"{form.fields[field].label}: {' '.join(errors)}")
    return redirect("dashboard:backup")


@login_required
@superuser_required
@require_GET
def storage_status(request):
    """Polled by the backup page while the host helper works on a stick."""
    status = backups.storage_status()
    return JsonResponse({"request": status.get("request", {}), "helper_running": status.get("helper_running")})


@login_required
@superuser_required
def usb_setup(request):
    """Setting a stick up as this device's backup stick: it is formatted - so this page names
    the stick and asks once more before the host helper is told to."""
    status = backups.storage_status()
    path = request.GET.get("disk") or request.POST.get("disk", "")
    disk = next((d for d in status.get("available", []) if d["path"] == path), None)
    if disk is None:
        messages.error(request, _("That stick is no longer there."))
        return redirect("dashboard:backup")
    if request.method == "POST":
        if not request.POST.get("confirm"):
            messages.error(request, _("Tick the box to confirm that the stick may be erased."))
        else:
            backups.storage_request("format", path=disk["path"], serial=disk["serial"],
                                    requested_by=request.user.get_username())
            audit.log(request, "backup.stick_setup", target=f"{disk['vendor']} {disk['model']}".strip(),
                      details=f"serial {disk['serial']}")
            return redirect(f"{reverse('dashboard:backup')}?working=format")
    return render(request, "dashboard/backup_usb_setup.html", {"disk": disk, "configured": backups.configured_stick()})


@login_required
@superuser_required
@require_POST
def usb_action(request):
    action = request.POST.get("action")
    if action not in ("adopt", "eject"):
        raise Http404
    backups.storage_request(action, requested_by=request.user.get_username())
    audit.log(request, f"backup.stick_{action}")
    return redirect(f"{reverse('dashboard:backup')}?working={action}")


def _backup_dir(source):
    return backups.own_backup_dir() if source == "own" else backups.foreign_backup_dir()


def _find(source, filename) -> Path:
    try:
        return backups.resolve(_backup_dir(source), filename)
    except backups.BackupError:
        raise Http404 from None


@login_required
@superuser_required
def backup_download(request, source, filename):
    target = _find(source, filename)
    audit.log(request, "backup.download", target=target.name)
    return FileResponse(open(target, "rb"), as_attachment=True, filename=target.name)


@login_required
@superuser_required
def backup_export(request):
    """A device backup of right now, straight into the browser - also without a stick.
    Encrypted with the device's passphrase like the ones on the stick."""
    settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.STORAGE_DIR) as tmp:
        try:
            target = backups.create_backup(Path(tmp))
        except backups.BackupError as exc:
            messages.error(request, _("Backup failed: %(error)s") % {"error": _(str(exc))})
            return redirect("dashboard:backup")
        data = target.read_bytes()
    audit.log(request, "backup.export", target=target.name)
    return HttpResponse(data, content_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{target.name}"'})


def _start(request, path: Path, name: str, form: RestoreForm, delete_after=False):
    restore_jobs.start_restore(path, name, request.user.get_username(), parts=form.cleaned_data["parts"],
                               secret=form.cleaned_data["passphrase"] or None, delete_after=delete_after)
    return redirect(f"{reverse('dashboard:backup')}#restore")


@login_required
@superuser_required
def backup_restore(request, source, filename):
    """Restoring a backup from a stick: what it brings back, then a confirmation. From
    another device's stick, everything is ticked - that is the replacement-device case."""
    target = _find(source, filename)
    device_backup = target.name.endswith(backups.ARCHIVE_SUFFIXES)
    initial = {"parts": list(restore_jobs.PARTS) if source == "foreign" and device_backup else ["database"]}
    form = RestoreForm(request.POST or None, initial=initial, device_backup=device_backup)
    if request.method == "POST" and form.is_valid():
        busy = _busy()
        if busy is None:
            return _start(request, target, target.name, form)
        messages.error(request, busy)
    return render(request, "dashboard/backup_restore.html", {
        "name": target.name, "source": source, "size": target.stat().st_size, "form": form,
        "encrypted": target.name.endswith(".enc"), "check": backups.verify(target),
    })


@login_required
@superuser_required
def backup_restore_upload(request):
    """A backup file from elsewhere: a download, or a file from another device's stick read on
    a PC. Checked before anything is touched: that it is a backup, that it can be opened,
    and that it isn't from a newer CoffeePy."""
    form = RestoreForm(request.POST or None, initial={"parts": list(restore_jobs.PARTS)})
    upload = request.FILES.get("backup")
    if request.method == "POST":
        if not upload:
            messages.error(request, _("Please choose a backup file."))
        elif form.is_valid():
            busy = _busy()
            if busy:
                messages.error(request, busy)
                return redirect("dashboard:backup")
            # On the data partition, not /tmp: a RAM-backed tmpfs on the Pi.
            directory = settings.STORAGE_DIR / "uploads"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{uuid.uuid4().hex}{''.join(Path(upload.name).suffixes)}"
            with open(target, "wb") as f:
                for chunk in upload.chunks():
                    f.write(chunk)
            try:
                with backups.open_backup(target, form.cleaned_data["passphrase"] or None) as backup:
                    backups.check_compatible(backup)
            except backups.BackupError as exc:
                target.unlink(missing_ok=True)
                messages.error(request, _(str(exc)))
            else:
                return _start(request, target, upload.name, form, delete_after=True)
    return render(request, "dashboard/backup_restore_upload.html", {"form": form})


@login_required
@superuser_required
@require_GET
def restore_status(request):
    return JsonResponse(restore_jobs.localized(restore_jobs.read_status()))
