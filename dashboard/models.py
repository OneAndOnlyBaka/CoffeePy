import datetime

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy


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


class NamePolicy(models.Model):
    """Names the kiosk refuses on top of coffee/names.py's own list of placeholders - the
    ones only someone who knows the team would think of. Edited on the dashboard's members
    page; one row."""

    blocked_names = models.TextField(blank=True, default="")

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def blocked_list(self) -> list[str]:
        return sorted({word.strip().lower() for word in self.blocked_names.replace(",", "\n").splitlines() if word.strip()})


class NightPolicy(models.Model):
    """When the kiosk sleeps - set on the dashboard's System page, carried out by the kiosk
    session itself (install/kiosk/openbox-autostart reads it from the file dashboard/night.py
    writes). One row; off until an admin turns it on. Saturday and Sunday can follow the
    weekdays, stay off all day, or have times of their own."""

    WEEKEND_SAME = "same"
    WEEKEND_ALL_DAY = "all_day"
    WEEKEND_TIMES = "times"
    WEEKEND_CHOICES = [
        (WEEKEND_SAME, gettext_lazy("Like the weekdays")),
        (WEEKEND_ALL_DAY, gettext_lazy("Off all day")),
        (WEEKEND_TIMES, gettext_lazy("Times of its own")),
    ]

    enabled = models.BooleanField(default=False)
    off_at = models.TimeField(default=datetime.time(20, 0))
    on_at = models.TimeField(default=datetime.time(5, 30))
    weekend = models.CharField(max_length=10, choices=WEEKEND_CHOICES, default=WEEKEND_SAME)
    weekend_off_at = models.TimeField(default=datetime.time(20, 0))
    weekend_on_at = models.TimeField(default=datetime.time(8, 0))

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]
