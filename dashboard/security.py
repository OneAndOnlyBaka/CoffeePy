"""Dashboard logins after the BSI's IT-Grundschutz (ORP.4 identity and access management):
password quality, a lock after repeated failed logins, logging out after inactivity, and
a password change an admin can require for the next login. The numbers are the admin's to
set (dashboard.models.PasswordPolicy, the "Login & passwords" page).

When the rules get stricter, existing passwords can't be checked - only their hashes are
stored. So each login checks the password it was just given against the current rules,
and one that falls short has to be replaced before the dashboard can be used."""
import string
import time
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import time_format
from django.utils.translation import gettext as _

from django.contrib.auth.password_validation import validate_password

from coffee.models import AuditLogEntry

from . import audit
from .models import MAX_PASSWORD_LENGTH, AccountSecurity, PasswordPolicy


def _kinds(password):
    return sum(any(c in group for c in password) for group in (
        string.ascii_lowercase, string.ascii_uppercase, string.digits)) + any(
        not c.isalnum() for c in password)


class BSIPasswordValidator:
    """Either long, or varied - the two ways the BSI's recommendation for good passwords
    describes, with the lengths and kinds (lower case, upper case, digits, others) from the
    PasswordPolicy. Common and all-digit passwords are refused besides (the other validators
    in settings.AUTH_PASSWORD_VALIDATORS)."""

    def validate(self, password, user=None):
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValidationError(_("At most %(max)s characters.") % {"max": MAX_PASSWORD_LENGTH}, code="password_too_long")
        policy = PasswordPolicy.current()
        kinds = _kinds(password)
        if policy.passphrase_length and len(password) >= policy.passphrase_length and kinds >= policy.passphrase_kinds:
            return
        if len(password) >= policy.min_length and kinds >= policy.min_kinds:
            return
        raise ValidationError(self.get_help_text(), code="password_quality")

    def get_help_text(self):
        policy = PasswordPolicy.current()
        text = _("At least %(length)s characters using %(kinds)s of the four kinds lower case, upper case, digits, "
                 "other characters") % {"length": policy.min_length, "kinds": policy.min_kinds}
        if policy.passphrase_length:
            text += " " + _("- or a passphrase of at least %(length)s characters using %(kinds)s of them") % {
                "length": policy.passphrase_length, "kinds": policy.passphrase_kinds}
        return text + "."


# --- locking after failed logins ---------------------------------------------------------

def lock_until(username):
    """When `username` may try again, or None. Counted from the audit log: the failed logins
    of the policy's lock period since the last successful one - so it holds across all gunicorn
    workers, and an unlock is simply time passing."""
    policy = PasswordPolicy.current()
    since = timezone.now() - timedelta(minutes=policy.lock_minutes)
    entries = AuditLogEntry.objects.filter(target=username, created_at__gte=since,
                                           action__in=("auth.login", "auth.login_failed")).order_by("-created_at")
    failures = []
    for entry in entries:
        if entry.action == "auth.login":
            break
        failures.append(entry.created_at)
    if len(failures) < policy.lock_after_failures:
        return None
    return failures[policy.lock_after_failures - 1] + timedelta(minutes=policy.lock_minutes)


class LockingAuthenticationForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username", "")
        until = lock_until(username) if username else None
        if until is not None:
            audit.log(self.request, "auth.locked", target=username, actor=username, outcome="failure")
            raise forms.ValidationError(
                _("Too many failed logins - this account is locked until %(time)s.")
                % {"time": time_format(timezone.localtime(until), "H:i")},
                code="locked")
        cleaned = super().clean()
        user = self.get_user()
        try:
            validate_password(self.cleaned_data.get("password", ""), user)
        except ValidationError:
            # Fine when it was set, too weak for today's rules: replace it before going on.
            AccountSecurity.objects.update_or_create(user=user, defaults={"must_change_password": True})
            audit.log(self.request, "auth.password_outdated", target=user.get_username(), actor=user.get_username(),
                      details="password doesn't meet the current rules - must be changed now")
        return cleaned


# --- in every dashboard request ---------------------------------------------------------

class DashboardSecurityMiddleware:
    """For logged-in dashboard users: log out after the policy's idle minutes without a request,
    and send someone who has to change their password to their account page until they
    have. Only /dashboard/ - the kiosk and the portal have their own sessions."""

    ALLOWED_WHILE_CHANGING = ("account", "logout", "login")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/dashboard/") and request.user.is_authenticated:
            now = time.time()
            last = request.session.get("dashboard_last_seen")
            idle_minutes = PasswordPolicy.current().idle_logout_minutes
            if last is not None and now - last > idle_minutes * 60:
                request._logout_reason = "logged out after inactivity"
                logout(request)
                messages.info(request, _("You were logged out after %(minutes)s minutes without activity.")
                              % {"minutes": idle_minutes})
                return redirect(f"{reverse('dashboard:login')}?next={request.path}")
            request.session["dashboard_last_seen"] = now
            # Resolved by path: the URL isn't resolved yet when middleware runs.
            allowed = {reverse(f"dashboard:{name}") for name in self.ALLOWED_WHILE_CHANGING}
            security = getattr(request.user, "security", None)  # none yet: nothing required
            if security is not None and security.must_change_password and request.path not in allowed:
                messages.info(request, _("Please set a new password first - it has to meet the current rules."))
                return redirect("dashboard:account")
        return self.get_response(request)
