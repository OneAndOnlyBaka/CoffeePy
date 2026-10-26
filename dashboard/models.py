from django.conf import settings
from django.db import models


class AccountSecurity(models.Model):
    """Per dashboard user: whether the next login has to set a new password first - asked
    for by an admin who set up the account or its password (dashboard.middleware)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="security")
    must_change_password = models.BooleanField(default=False)

    @classmethod
    def for_user(cls, user):
        return cls.objects.get_or_create(user=user)[0]


# The hard limits of what the policy page accepts - beyond them a setting stops making
# sense (a 3-character minimum) or turns against its users (a 100-character one). Nothing
# past MAX_PASSWORD_LENGTH is accepted as a password at all: longer is no safer, only a way
# to make the server hash megabytes.
MAX_PASSWORD_LENGTH = 128
POLICY_LIMITS = {
    "min_length": (8, 64),
    "min_kinds": (1, 4),
    "passphrase_length": (0, 128),  # 0: no passphrase rule
    "passphrase_kinds": (1, 4),
    "lock_after_failures": (3, 20),
    "lock_minutes": (1, 1440),
    "idle_logout_minutes": (5, 480),
}


class PasswordPolicy(models.Model):
    """The rules for dashboard logins (dashboard/security.py), set on the dashboard's
    "Login & passwords" page. One row; the defaults follow the BSI's recommendation:
    12+ characters from three kinds, or a passphrase of 20+ from two."""

    min_length = models.PositiveSmallIntegerField(default=12)
    min_kinds = models.PositiveSmallIntegerField(default=3)
    passphrase_length = models.PositiveSmallIntegerField(default=20)
    passphrase_kinds = models.PositiveSmallIntegerField(default=2)
    lock_after_failures = models.PositiveSmallIntegerField(default=5)
    lock_minutes = models.PositiveSmallIntegerField(default=15)
    idle_logout_minutes = models.PositiveSmallIntegerField(default=30)

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]
