import secrets

from django.db import models
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.translation import gettext as _, gettext_lazy


class CoffeeSort(models.Model):
    name = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    machine_strokes = models.PositiveIntegerField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Member(models.Model):
    uid = models.BigIntegerField(primary_key=True)
    nickname = models.CharField(max_length=100, blank=True)
    favourite_coffee = models.ForeignKey(
        CoffeeSort, null=True, blank=True, on_delete=models.SET_NULL
    )
    # Set via the dashboard's "Mark as departed" action for someone who left without
    # settling up - acknowledges/writes off the debt (excluded from the pillory and
    # rankings from then on) and deactivates their card(s) rather than deleting their
    # history. Null means still an active member.
    departed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.nickname or str(self.uid)

    def display_name(self) -> str:
        return self.nickname or str(self.uid)

    @property
    def name_problem(self) -> str | None:
        """Why this member's name won't do (a key of coffee.names.MESSAGES), or None. The
        kiosk asks for a proper one before the next order - which also covers everyone who
        is still just their card number (the kiosk used to fill an empty name field with it)."""
        from coffee import names  # noqa: PLC0415 - names imports this module

        return names.problem(self.nickname, member=self)

    @property
    def is_departed(self) -> bool:
        return self.departed_at is not None


class MemberCard(models.Model):
    """An additional physical card linked to a member, on top of their primary `uid` -
    e.g. a lost/replaced card ("Set alt card"), or another account's card folded in via
    "Merge members". A member can have any number of these - replaces what used to be a
    single `Member.alt_uid` field, which could only ever hold one."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="alt_cards")
    uid = models.BigIntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return str(self.uid)


class AltUidCapture(models.Model):
    """Arms the kiosk to link the next card tapped as an additional card for this member
    (see MemberCard), instead of a dashboard form field asking a human to type a card's
    numeric UID from memory. Dashboard (member_alt_uid_start/_poll) and kiosk
    (views._handle_new_scan) run in separate browser sessions, so this has to be shared
    via the DB rather than a session. Only one row is meant to exist at a time (single
    physical reader) - member_alt_uid_start enforces that by clearing any previous row
    before arming."""

    member = models.OneToOneField(Member, on_delete=models.CASCADE, related_name="pending_alt_uid_capture")
    expires_at = models.DateTimeField()
    # Set by kiosk.views._try_assign_alt_uid when a tapped card can't be applied (e.g.
    # already linked to someone else) - shown both on the kiosk itself and wherever this
    # capture was armed from (dashboard poll, portal poll), so a bad tap doesn't just
    # look like nothing happened. Left in place (not cleared) until a *different* tap
    # succeeds or the whole capture expires - see is_expired().
    last_error = models.CharField(max_length=200, blank=True)

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


def _generate_link_token() -> str:
    return secrets.token_urlsafe(32)


class MemberLinkToken(models.Model):
    """A short-lived, single-use bootstrap link (see the "Link this phone" QR code in
    kiosk Settings) that, once opened, establishes a long-lived portal.Member session on
    that device - see MemberDevice. The token itself is only ever used once and expires
    quickly; it isn't the ongoing auth mechanism, just the handoff into one."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="link_tokens")
    token = models.CharField(max_length=64, unique=True, default=_generate_link_token)
    expires_at = models.DateTimeField()

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class MemberDevice(models.Model):
    """One row per browser session created via a MemberLinkToken - lets a member (or an
    admin) see/revoke individual linked devices instead of only being able to nuke
    Django's session table blindly. `session_key` ties this to the actual
    django.contrib.sessions.models.Session row that holds the real auth state; deleting
    that row (see portal views) is what actually logs the device out - this row is just
    bookkeeping for the revoke UI."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="devices")
    session_key = models.CharField(max_length=40, unique=True)
    label = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen_at"]


class Order(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="orders")
    coffee_sort = models.ForeignKey(
        CoffeeSort, null=True, blank=True, on_delete=models.SET_NULL
    )
    value = models.DecimalField(max_digits=8, decimal_places=2)
    machine_strokes = models.PositiveIntegerField()
    # default=timezone.now (not auto_now_add) so import_sqlite can set historical
    # timestamps explicitly; it still defaults to "now" for normal orders.
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    info = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.member_id} - {self.value} ({self.created_at:%Y-%m-%d})"


CASH_METHOD_NAME = "Cash"


def payment_method_label(name: str) -> str:
    """How a payment method name reads on screen. Cash is stored as "Cash" whatever the
    language (Payment.method_name is data, and a German "Bar" would no longer match it),
    so only its label is translated; every other name is the admin's own and shown as is."""
    from django.utils.translation import gettext

    return gettext("Cash") if not name or name == CASH_METHOD_NAME else name


class PaymentMethod(models.Model):
    """Admin-configured way a member can pay, in addition to the always-available cash
    option (CASH_METHOD_NAME, which isn't a row here - it can't be deleted or
    misconfigured). The three built-in `provider` presets need only `identifier` filled
    in (a paypal.me username, a pool URL/ID, or a WERO phone number) and build a correct
    link/instruction on their own; PROVIDER_CUSTOM is the escape hatch for anything else,
    using `link_template` with an optional literal "{amount}" placeholder."""

    PROVIDER_PAYPAL_ME = "paypal_me"
    PROVIDER_PAYPAL_POOL = "paypal_pool"
    PROVIDER_WERO = "wero"
    PROVIDER_CUSTOM = "custom"
    PROVIDER_CHOICES = [
        (PROVIDER_PAYPAL_ME, gettext_lazy("PayPal (Friends & Family / paypal.me)")),
        (PROVIDER_PAYPAL_POOL, "PayPal Pool"),
        (PROVIDER_WERO, "WERO"),
        (PROVIDER_CUSTOM, gettext_lazy("Custom link")),
    ]

    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_CUSTOM)
    identifier = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "paypal.me username for PayPal; pool URL (or just its ID) for a PayPal Pool; "
            "phone number registered with WERO for WERO. Not used for a custom link."
        ),
    )
    link_template = models.CharField(
        max_length=500,
        blank=True,
        help_text='Custom provider only. May contain "{amount}", filled in with what is owed.',
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def build_link(self, amount) -> str:
        """A scannable/clickable link prefilled with `amount`, or "" if this method has
        none (e.g. WERO, which has no public deep-link scheme - see instructions())."""
        if self.provider == self.PROVIDER_PAYPAL_ME:
            username = self.identifier.strip().lstrip("@")
            return f"https://paypal.me/{username}/{amount:.2f}EUR" if username else ""
        if self.provider == self.PROVIDER_PAYPAL_POOL:
            pool = self.identifier.strip()
            if not pool:
                return ""
            return pool if pool.startswith("http") else f"https://www.paypal.com/pools/c/{pool}"
        if self.provider == self.PROVIDER_WERO:
            return ""
        if "{amount}" in self.link_template:
            return self.link_template.format(amount=f"{amount:.2f}")
        return self.link_template

    def instructions(self, amount) -> str:
        """Human-readable fallback for methods with no scannable link (build_link() ==
        "") - e.g. WERO's registered phone number - so there's still something to show
        on the pay screens."""
        if self.provider == self.PROVIDER_WERO and self.identifier:
            return _("Send %(amount)s€ via WERO to %(identifier)s") % {
                "amount": number_format(amount, 2),
                "identifier": self.identifier,
            }
        return ""


class Payment(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="payments")
    value = models.DecimalField(max_digits=8, decimal_places=2)
    # Snapshot of the method name at payment time (not a FK) so historical stats stay
    # correct even if a PaymentMethod is later renamed or deleted.
    method_name = models.CharField(max_length=100, default=CASH_METHOD_NAME, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    info = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.member_id} - {self.value} ({self.created_at:%Y-%m-%d})"


class NfcScan(models.Model):
    EVENT_INSERTED = "card_inserted"
    EVENT_REMOVED = "card_removed"
    EVENT_CHOICES = [
        (EVENT_INSERTED, "Card inserted"),
        (EVENT_REMOVED, "Card removed"),
    ]

    uid = models.CharField(max_length=64, null=True, blank=True)
    event = models.CharField(max_length=32, choices=EVENT_CHOICES)
    atr = models.CharField(max_length=255, blank=True)
    card_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.event} {self.uid} @ {self.created_at:%H:%M:%S}"

    def uid_as_int(self):
        """Bridge hex UID -> int, little-endian byte order to match the legacy
        CardScanner.UIDConverter.ToInt() so previously issued card UIDs keep resolving
        to the same Member.uid after the SQLite import."""
        if not self.uid:
            return None
        try:
            raw = bytes.fromhex(self.uid)
        except ValueError:
            return None
        return int.from_bytes(raw, byteorder="little")


class AuditLogEntry(models.Model):
    """One row per admin action taken through the dashboard - see dashboard.audit.log(),
    called from the views themselves right after each mutation succeeds. `actor` and
    `target` are plain strings, not FKs: the whole point is to still read sensibly after
    the acting user or the affected row (a deleted/merged member, a removed coffee sort)
    no longer exists."""

    SOURCE_DASHBOARD, SOURCE_KIOSK, SOURCE_PORTAL, SOURCE_SYSTEM = "dashboard", "kiosk", "portal", "system"
    SOURCE_CHOICES = [(SOURCE_DASHBOARD, "Dashboard"), (SOURCE_KIOSK, "Kiosk"), (SOURCE_PORTAL, "Portal"),
                      (SOURCE_SYSTEM, "System")]
    OUTCOME_SUCCESS, OUTCOME_FAILURE = "success", "failure"

    action = models.CharField(max_length=100, db_index=True)
    actor = models.CharField(max_length=150, blank=True)
    target = models.CharField(max_length=200, blank=True)
    details = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Where it happened and from which address (BSI OPS.1.1.5: who, what, when, where, and
    # whether it worked) - a failed login matters as much as a successful one.
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_DASHBOARD)
    ip = models.GenericIPAddressField(null=True, blank=True)
    outcome = models.CharField(max_length=10, default=OUTCOME_SUCCESS)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor}: {self.action} {self.target}".strip()


class NfcReaderStatus(models.Model):
    """Singleton row (always pk=1) mirroring the acr122u-mqtt bridge's retained
    <MQTT_TOPIC>/status message (see nfc-cardreader/bridge.py's MQTT last-will/on-connect
    publish and coffee.management.commands.mqtt_listener, which is the only writer).
    Defaults to offline via get_or_create - that's the right default both the first time
    the bridge has never connected at all and while it's genuinely down, rather than
    silently assuming a working reader with no evidence either way."""

    is_online = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "online" if self.is_online else "offline"


class BackupSettings(models.Model):
    """How often the backup service backs up to the stick, and which backups it keeps -
    set on the dashboard's backup page. One row, starting from the defaults below."""

    interval_minutes = models.PositiveIntegerField(default=60)
    # Generations (coffee.backups.rotate): the newest few, then one per day, per week, per
    # month - so a mistake noticed only days later can still be undone.
    keep_recent = models.PositiveIntegerField(default=48)
    keep_daily = models.PositiveIntegerField(default=14)
    keep_weekly = models.PositiveIntegerField(default=8)
    keep_monthly = models.PositiveIntegerField(default=12)

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]
