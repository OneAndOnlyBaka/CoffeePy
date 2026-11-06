import secrets

from django.db import models
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.translation import gettext as _, gettext_lazy


class CoffeeSort(models.Model):
    name = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    machine_strokes = models.PositiveIntegerField()
    # Set by the coffee machine sync (coffee.machine.sync_products): a sort the connected machine
    # doesn't make is no longer offered at the kiosk - its orders and statistics stay. Cleared again
    # when the machine is switched off in the dashboard.
    is_obsolete = models.BooleanField(default=False)
    # What the machine has nothing to do with (tea, a biscuit) and stays offered all the same.
    without_machine = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def offered(cls):
        """The sorts the kiosk offers - every one, unless the machine sync made it obsolete."""
        return cls.objects.filter(is_obsolete=False)


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
    # One of the "selected people" who may correct the stock at the kiosk and in the portal
    # (dashboard → Members). Only matters while the inventory is switched on.
    may_take_stock = models.BooleanField(default=False)

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
# The credit a Purchase books: paid in kind, not in money. Never offered as a way to pay -
# only Purchase creates (and edits, and deletes) these.
PURCHASE_METHOD_NAME = "Purchase"


def payment_method_label(name: str) -> str:
    """How a payment method name reads on screen. Cash is stored as "Cash" whatever the
    language (Payment.method_name is data, and a German "Bar" would no longer match it),
    so only its label is translated; every other name is the admin's own and shown as is."""
    from django.utils.translation import gettext, pgettext

    if not name or name == CASH_METHOD_NAME:
        return gettext("Cash")
    if name == PURCHASE_METHOD_NAME:
        return pgettext("payment method", "Purchase")
    return name


class PaymentMethod(models.Model):
    """Admin-configured way a member can pay, in addition to the always-available cash
    option (CASH_METHOD_NAME, which isn't a row here - it can't be deleted or
    misconfigured). The PayPal presets need only `identifier` (a paypal.me username, a pool
    URL/ID) and build a correct link on their own; a GiroCode needs recipient and IBAN and
    becomes the EPC QR code banking apps turn into a prefilled transfer; PROVIDER_CUSTOM is the
    escape hatch for anything else, using `link_template` with an optional "{amount}"."""

    PROVIDER_PAYPAL_ME = "paypal_me"
    PROVIDER_PAYPAL_POOL = "paypal_pool"
    PROVIDER_GIROCODE = "girocode"
    PROVIDER_CUSTOM = "custom"
    PROVIDER_CHOICES = [
        (PROVIDER_PAYPAL_ME, gettext_lazy("PayPal (Friends & Family / paypal.me)")),
        (PROVIDER_PAYPAL_POOL, "PayPal Pool"),
        (PROVIDER_GIROCODE, gettext_lazy("GiroCode (bank transfer by QR code)")),
        (PROVIDER_CUSTOM, gettext_lazy("Custom link")),
    ]
    # What each provider needs filled in - the form shows only these (dashboard/forms.py).
    PROVIDER_FIELDS = {
        PROVIDER_PAYPAL_ME: ("identifier",),
        PROVIDER_PAYPAL_POOL: ("identifier",),
        PROVIDER_GIROCODE: ("recipient", "iban", "bic", "purpose"),
        PROVIDER_CUSTOM: ("link_template",),
    }
    # A GiroCode's purpose may name the member who pays, so the transfers can be told apart.
    MEMBER_PLACEHOLDER = "{member}"

    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_CUSTOM)
    identifier = models.CharField(
        max_length=300,
        blank=True,
        help_text="paypal.me username for PayPal; pool URL (or just its ID) for a PayPal Pool.",
    )
    link_template = models.CharField(
        max_length=500,
        blank=True,
        help_text='Custom provider only. May contain "{amount}", filled in with what is owed.',
    )
    # GiroCode only - the limits are the EPC QR code's (EPC069-12).
    recipient = models.CharField(max_length=70, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    bic = models.CharField(max_length=11, blank=True)
    purpose = models.CharField(max_length=140, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def build_link(self, amount) -> str:
        """A clickable link prefilled with `amount`, or "" if this method has none - a GiroCode
        is a QR code for a banking app, not a link (qr_payload)."""
        if self.provider == self.PROVIDER_PAYPAL_ME:
            username = self.identifier.strip().lstrip("@")
            return f"https://paypal.me/{username}/{amount:.2f}EUR" if username else ""
        if self.provider == self.PROVIDER_PAYPAL_POOL:
            pool = self.identifier.strip()
            if not pool:
                return ""
            return pool if pool.startswith("http") else f"https://www.paypal.com/pools/c/{pool}"
        if self.provider == self.PROVIDER_GIROCODE:
            return ""
        if "{amount}" in self.link_template:
            return self.link_template.format(amount=f"{amount:.2f}")
        return self.link_template

    def purpose_for(self, member=None) -> str:
        name = member.display_name() if member is not None else ""
        return self.purpose.replace(self.MEMBER_PLACEHOLDER, name).strip()[:140]

    def qr_payload(self, amount, member=None) -> str:
        """What the kiosk's QR code for this method carries: the link, or for a GiroCode the
        EPC QR text (version 002, UTF-8, SEPA credit transfer) - recipient, IBAN, amount and
        purpose, which a banking app fills into a transfer. "" when there is nothing to scan."""
        if self.provider != self.PROVIDER_GIROCODE:
            return self.build_link(amount)
        if not (self.recipient and self.iban):
            return ""
        lines = ["BCD", "002", "1", "SCT", self.bic, self.recipient, self.iban, f"EUR{amount:.2f}",
                 "", "", self.purpose_for(member)]
        return "\n".join(lines).rstrip("\n")

    def transfer_details(self, amount, member=None) -> list[tuple[str, str]]:
        """A GiroCode's transfer to copy by hand - the phone can't scan itself (portal)."""
        if self.provider != self.PROVIDER_GIROCODE or not (self.recipient and self.iban):
            return []
        details = [(_("Recipient"), self.recipient),
                   ("IBAN", " ".join(self.iban[i:i + 4] for i in range(0, len(self.iban), 4)))]
        if self.bic:
            details.append(("BIC", self.bic))
        details.append((_("Amount"), f"{number_format(amount, 2)}€"))
        if purpose := self.purpose_for(member):
            details.append((_("Purpose"), purpose))
        return details


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


class InventoryItem(models.Model):
    """Something the members buy for everyone - coffee beans, milk, cookies. The admins keep
    the list (dashboard → Inventory); a member only picks from it."""

    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Purchase(models.Model):
    """`quantity` of `item` bought for `price`. Paid by a member, who is credited that price in
    full through `payment` (method PURCHASE_METHOD_NAME) - which is what puts it into every balance
    without each of them having to know about purchases. Without a member it was paid from the
    coffee box: no payment, only stock and spending (InventoryPolicy "dashboard")."""

    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.CASCADE, related_name="purchases")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="purchases")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    payment = models.OneToOneField(Payment, null=True, blank=True, on_delete=models.CASCADE, related_name="purchase")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    info = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.member_id or 'box'} - {self.quantity} {self.item} ({self.created_at:%Y-%m-%d})"


class StockCount(models.Model):
    """What a stock-take found on the shelf. The stock is the newest count plus the purchases
    since (services.stock_levels) - nothing is taken off per cup."""

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="counts")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    # Who counted, as the audit log names them ("Anna B." or a dashboard login) - a member or
    # a dashboard user may be deleted later, the count stays.
    counted_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


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


class MachineStatus(models.Model):
    """Singleton row (always pk=1): what the coffee machine bridge last reported (coffee-machine/,
    its retained MQTT topics) - written by mqtt_listener only, read by the kiosk and the dashboard.
    Like NfcReaderStatus it is left alone by a reset: it describes the device, not its data."""

    online = models.BooleanField(default=False)  # the bridge runs and reaches the broker
    status = models.CharField(max_length=20, blank=True)  # online / offline / disabled, as sent
    state = models.JSONField(default=dict)  # connected, model, alerts, progress, ... (README)
    counters = models.JSONField(default=dict)
    products = models.JSONField(default=dict)
    scan = models.JSONField(default=dict)  # the answer to the dashboard's last search
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    @property
    def connected(self) -> bool:
        return self.online and bool(self.state.get("connected"))


class MachineProduct(models.Model):
    """A product the connected coffee machine makes (its own list, coffee-machine/ `products`), and
    the coffee sort it is sold as. Kept when the machine no longer lists it (`present` off), so a
    machine that comes back - or a restore - finds its links again."""

    code = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=100)
    strokes = models.PositiveSmallIntegerField(default=1)
    present = models.BooleanField(default=True)
    brewable = models.BooleanField(default=True)  # the bridge knows a recipe it can send
    water_ml = models.PositiveSmallIntegerField(null=True, blank=True)  # the definition's default
    coffee_sort = models.OneToOneField(CoffeeSort, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="machine_product")
    # Free: offered at the kiosk without a card and without a booking (hot water, milk foam) - and,
    # with the keys locked, the only way to get it at all.
    free = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MachineDispense(models.Model):
    """One product the machine made, or was asked for and didn't: from the kiosk (booked as an order
    once done), free at the kiosk, from Home Assistant, or started at the machine itself."""

    SOURCE_KIOSK, SOURCE_FREE, SOURCE_HA, SOURCE_MANUAL = "app", "free", "ha", "manual"
    SOURCES = [(SOURCE_KIOSK, gettext_lazy("Kiosk")), (SOURCE_FREE, gettext_lazy("Free at the kiosk")),
               (SOURCE_HA, gettext_lazy("Home Assistant")), (SOURCE_MANUAL, gettext_lazy("At the machine"))]
    REQUESTED, STARTED, DONE, FAILED = "requested", "started", "done", "failed"
    STATES = [(REQUESTED, gettext_lazy("asked for")), (STARTED, gettext_lazy("running")),
              (DONE, gettext_lazy("done")), (FAILED, gettext_lazy("failed"))]

    request_id = models.CharField(max_length=40, blank=True, db_index=True)
    code = models.PositiveSmallIntegerField()
    product_name = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=10, choices=SOURCES)
    state = models.CharField(max_length=10, choices=STATES, default=REQUESTED)
    reason = models.CharField(max_length=200, blank=True)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="dispenses")
    coffee_sort = models.ForeignKey(CoffeeSort, null=True, blank=True, on_delete=models.SET_NULL)
    order = models.OneToOneField(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="dispense")
    water_ml = models.PositiveSmallIntegerField(null=True, blank=True)
    strength = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.product_name or self.code} ({self.source}, {self.state})"


class MachineCounter(models.Model):
    """The machine's own product counters at one moment - what the inventory reckons consumption
    from (each product counted since a stock-take times what it takes), also for products made past
    the tally. One row per reading that changed something."""

    taken_at = models.DateTimeField(default=timezone.now, db_index=True)
    total = models.PositiveIntegerField()
    products = models.JSONField(default=dict)  # {"<code>": count}
    maintenance = models.JSONField(default=dict)

    class Meta:
        ordering = ["-taken_at"]


class MachineSupply(models.Model):
    """What the machine has left of water, grounds space and beans - predicted, as nothing in it
    measures them: every product made takes its share (its water from the brew's own recipe, a
    portion of grounds, the beans per portion), and the machine's message going away (the tank put
    back, the container emptied) or a tap at the kiosk refills it. coffee.machine.SUPPLIES."""

    WATER, GROUNDS, BEANS = "water", "grounds", "beans"
    KINDS = [(WATER, gettext_lazy("Water")), (GROUNDS, gettext_lazy("Grounds")), (BEANS, gettext_lazy("Beans"))]

    kind = models.CharField(max_length=10, choices=KINDS, unique=True)
    capacity = models.PositiveIntegerField()  # ml of water, portions of grounds, g of beans
    used = models.FloatField(default=0)
    # What one portion of coffee takes, for the beans (g); unused for the others.
    per_portion = models.FloatField(default=0)
    reset_at = models.DateTimeField(default=timezone.now)

    @property
    def used_percent(self) -> int:
        return min(100, round(self.used * 100 / self.capacity)) if self.capacity else 0

    @property
    def left_percent(self) -> int:
        return 100 - self.used_percent


class MachineIncident(models.Model):
    """The machine asked for something (water, grounds, drip tray, beans) during or right after a
    member's brew - theirs to do. Not done in time, they "ran off"; whoever came next and it was done
    by then is the hero (a guess - nobody can tell who really did it, so it only ever counts for
    someone). The kiosk's "Ran off" board, if the admin switches it on (KioskBoards)."""

    OPEN, FIXED, LEFT = "open", "fixed", "left"
    OUTCOMES = [(OPEN, gettext_lazy("open")), (FIXED, gettext_lazy("done")), (LEFT, gettext_lazy("ran off"))]

    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="machine_incidents")
    alert = models.CharField(max_length=40)
    dispense = models.ForeignKey("MachineDispense", null=True, blank=True, on_delete=models.SET_NULL)
    raised_at = models.DateTimeField(default=timezone.now, db_index=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=OUTCOMES, default=OPEN)
    # The last member at the kiosk after the grace while it stood open - who most likely did it.
    hero = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="machine_heroics")

    class Meta:
        ordering = ["-raised_at"]


class InventoryConsumption(models.Model):
    """How much of an item one product of the coffee machine takes (8 g of beans an espresso, 100 ml
    of milk a cappuccino) - with the machine's counters that is what was used since a stock-take,
    and so the stock there should be (services.stock_levels "expected"), past the tally too."""

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="consumption")
    product = models.ForeignKey(MachineProduct, on_delete=models.CASCADE, related_name="consumption")
    amount = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "product"], name="one_consumption_per_item_product")]


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
