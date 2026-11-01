"""The audit log: one row per security-relevant event on the device (BSI OPS.1.1.5) - who,
what, when, from where, and whether it worked.

Everything that changes data writes one, right after the change succeeded: the dashboard,
the kiosk (card logins, orders, payments, settings), the portal (phone logins, payments,
devices). Logins, logouts, failed logins, lockouts and refused access are logged by the
auth signals below and the dashboard's own decorators. Events on the host - a backup stick
plugged in or formatted, an update applied - are written by those host processes to
data/storage/events.jsonl and collected by ingest_host_events(). Rows older than
AUDIT_RETENTION_DAYS are deleted (manage.py audit_housekeeping, run by the backup service).
"""
import json
import os
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from coffee.models import AuditLogEntry

SOURCES_BY_PATH = (("/kiosk/", AuditLogEntry.SOURCE_KIOSK), ("/portal/", AuditLogEntry.SOURCE_PORTAL),
                   ("/dashboard/", AuditLogEntry.SOURCE_DASHBOARD))


def client_ip(request):
    """The client's address. Only Caddy reaches the app, and it puts the real client in
    X-Forwarded-For - REMOTE_ADDR is always Caddy's own container."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None
    return ip


def source_of(request):
    path = request.path
    return next((source for prefix, source in SOURCES_BY_PATH if path.startswith(prefix)), AuditLogEntry.SOURCE_DASHBOARD)


def log(request, action: str, target: str = "", details: str = "", outcome: str = AuditLogEntry.OUTCOME_SUCCESS,
        actor: str | None = None) -> None:
    """Records one audit row for something done in a request. `action` is a short dotted
    slug ("member.delete"); `target` names what was affected in human terms; `actor` defaults
    to the logged-in dashboard user - the kiosk and the portal pass the member instead."""
    if actor is None:
        actor = request.user.get_username() if getattr(request, "user", None) and request.user.is_authenticated else ""
    AuditLogEntry.objects.create(action=action, actor=(actor or "")[:150], target=str(target or "")[:200],
                                 details=str(details or "")[:500], outcome=outcome, source=source_of(request),
                                 ip=client_ip(request))


def log_actor(actor: str, action: str, target: str = "", details: str = "",
              outcome: str = AuditLogEntry.OUTCOME_SUCCESS, source: str = AuditLogEntry.SOURCE_SYSTEM) -> None:
    """Same as `log()`, for work outside a request (the import thread, the backup service)."""
    AuditLogEntry.objects.create(action=action, actor=actor[:150], target=target[:200], details=details[:500],
                                 outcome=outcome, source=source)


def member_actor(member) -> str:
    return f"{member.display_name()} (uid {member.uid})" if member is not None else ""


# --- logins -----------------------------------------------------------------------------

@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    if request is not None:
        log(request, "auth.login", target=user.get_username(), actor=user.get_username())


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if request is not None and user is not None:
        reason = getattr(request, "_logout_reason", "")
        log(request, "auth.logout", target=user.get_username(), actor=user.get_username(), details=reason)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    if request is not None:
        name = credentials.get("username", "")
        log(request, "auth.login_failed", target=name, actor=name, outcome=AuditLogEntry.OUTCOME_FAILURE)


# --- events from the host ----------------------------------------------------------------

def ingest_host_events() -> int:
    """Moves the host's events (data/storage/events.jsonl, appended by storage_helper.py and
    update_watcher.py) into the log. The file is renamed before it is read, so a line the
    host appends meanwhile goes to a fresh file and is collected next time."""
    path = settings.STORAGE_DIR / "events.jsonl"
    claimed = path.with_name(f"events.{os.getpid()}.ingesting")
    try:
        os.replace(path, claimed)
    except OSError:
        return 0
    count = 0
    try:
        for line in claimed.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            entry = AuditLogEntry.objects.create(
                action=str(event.get("action", "host.event"))[:100], actor=str(event.get("actor", "system"))[:150],
                target=str(event.get("target", ""))[:200], details=str(event.get("details", ""))[:500],
                outcome=event.get("outcome") or AuditLogEntry.OUTCOME_SUCCESS, source=AuditLogEntry.SOURCE_SYSTEM)
            when = event.get("time")
            if when:  # keep when it happened, not when it was collected
                AuditLogEntry.objects.filter(pk=entry.pk).update(created_at=datetime.fromtimestamp(when, tz=dt_timezone.utc))
            count += 1
    finally:
        claimed.unlink(missing_ok=True)
    return count


def prune(days: int | None = None) -> int:
    days = days if days is not None else int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
    if days <= 0:
        return 0
    deleted, _ = AuditLogEntry.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted
