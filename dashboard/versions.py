"""Everything that tells exactly which software runs on this device, for the update page's
version list - and as one block of text to paste into a bug report. Without a tag the commit
hash, and for every container the id of its image, are what tell two builds apart.

Three sources: the app's own coffeepy-version.json (scripts/write_version.sh), what this
container can ask for itself, and the host's data/updates/system.json - the update watcher
(install/update_watcher.py) writes it, since only the host can see the images and the OS."""
import platform
import subprocess
from datetime import datetime
from importlib import metadata

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext as _


def _package(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def _database():
    if connection.vendor != "postgresql":
        return f"{connection.vendor} {connection.Database.sqlite_version}" if connection.vendor == "sqlite" else ""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version')")
        return f"PostgreSQL {cursor.fetchone()[0]}"


def _pg_dump():
    try:
        result = subprocess.run(["pg_dump", "--version"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode(errors="replace").strip() if result.returncode == 0 else ""


def _when(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M")


def _os_bundle(applied):
    if not applied.get("version"):
        return ""
    return f"{applied['version']} ({_('applied')} {_when(applied['applied_at'])})" if applied.get("applied_at") else applied["version"]


def sections(installed, system):
    """[(heading, [(label, value), ...]), ...] - empty values left out."""
    build = settings.COFFEEPY_BUILD
    app = [
        (_("Version"), settings.COFFEEPY_VERSION),
        (_("Commit"), build.get("commit", "")),
        (_("Branch"), build.get("branch", "")),
        (_("Committed"), build.get("committed", "")),
    ]
    if installed.get("version"):
        applied = f" ({_('applied')} {_when(installed['applied_at'])})" if installed.get("applied_at") else ""
        app.append((_("Installed by update"), f"{installed['version']}{applied}"))
    runtime = [
        ("Python", platform.python_version()),
        ("Django", _package("django")),
        ("gunicorn", _package("gunicorn")),
        (_("Database"), _database()),
        ("pg_dump", _pg_dump()),
    ]
    result = [(_("CoffeePy"), app), (_("App container"), runtime)]
    if system:
        signature = system.get("signature") or {}
        if not signature.get("check", True):
            checked = _("off - every update is installed")
        elif signature.get("key") == "missing":
            checked = _("no key on the device")
        else:
            key = _("own key") if signature.get("key") == "own" else _("CoffeePy's key")
            checked = f"{key} {signature.get('fingerprint', '')}".strip()
        result.append((_("Device"), [
            (_("Model"), system.get("model", "")),
            (_("Operating system"), system.get("os", "")),
            (_("Kernel"), system.get("kernel", "")),
            (_("Platform"), system.get("platform", "")),
            ("Docker", system.get("docker", "")),
            ("Docker Compose", system.get("compose", "")),
            (_("Update signature"), checked),
            (_("OS bundle"), _os_bundle(system.get("os_bundle") or {})),
            (_("As of"), _when(system["updated"]) if system.get("updated") else ""),
        ]))
        result.append((_("Containers"), [
            (c.get("service", ""), " ".join(part for part in (
                c.get("image", ""), f"@{c['image_id']}" if c.get("image_id") else "",
                f"({c['state']})" if c.get("state") and c["state"] != "running" else "") if part))
            for c in system.get("containers", [])
        ]))
    return [(heading, [(label, value) for label, value in rows if value]) for heading, rows in result]


def as_text(sections):
    """The same list as plain text - what the copy button puts on the clipboard."""
    blocks = []
    for heading, rows in sections:
        if rows:
            blocks.append("\n".join([f"[{heading}]"] + [f"{label}: {value}" for label, value in rows]))
    return "\n\n".join(blocks) + "\n"
