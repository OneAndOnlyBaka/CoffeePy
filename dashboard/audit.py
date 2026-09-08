from coffee.models import AuditLogEntry


def log(request, action: str, target: str = "", details: str = "") -> None:
    """Records one audit-log row for a dashboard action that just succeeded. `action` is
    a short dotted slug (e.g. "member.delete"); `target` identifies what was affected in
    human terms (a member's display name, a coffee sort's name, ...)."""
    actor = request.user.get_username() if request.user.is_authenticated else ""
    log_actor(actor, action, target=target, details=details)


def log_actor(actor: str, action: str, target: str = "", details: str = "") -> None:
    """Same as `log()`, for background work that outlives its request (the legacy-DB
    import) and therefore has to name the actor itself."""
    AuditLogEntry.objects.create(action=action, actor=actor, target=target, details=details)
