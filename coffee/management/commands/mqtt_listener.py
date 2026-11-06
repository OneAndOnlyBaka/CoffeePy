import json
import logging
import os
import time

import paho.mqtt.client as mqtt
from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections
from django.utils import timezone

from coffee import machine
from dashboard import audit
from coffee.models import MachineStatus, NfcReaderStatus, NfcScan

log = logging.getLogger("coffee.mqtt_listener")

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "nfc/acr122u")
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "coffeepy-django")
STATUS_TOPIC = f"{MQTT_TOPIC}/status"

SCAN_RETENTION_SECONDS = 3600
STATUS_RETRY_SECONDS = 5
STATUS_CHECK_SECONDS = 30


def handle_payload(raw: bytes):
    """Parse one MQTT message from the acr122u-mqtt bridge and persist it as an NfcScan.
    Returns the created NfcScan, or None if the payload wasn't a card event."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        log.warning("Ignoring non-JSON MQTT payload: %r", raw[:200])
        return None

    event = payload.get("event")
    if event not in (NfcScan.EVENT_INSERTED, NfcScan.EVENT_REMOVED):
        return None

    return NfcScan.objects.create(
        uid=payload.get("uid"),
        event=event,
        atr=payload.get("atr") or "",
        card_type=payload.get("card_type") or "",
    )


def set_reader_online(is_online: bool) -> None:
    """Updates the singleton NfcReaderStatus row the kiosk/dashboard read from - see
    coffee.models.NfcReaderStatus for why this defaults to offline until told
    otherwise."""
    NfcReaderStatus.objects.update_or_create(pk=1, defaults={"is_online": is_online})


class StoredStatus:
    """A status a bridge reports, kept until the database takes it. The bridges send it once,
    retained, and again only when it changes - after a power cut this process got it while
    PostgreSQL was still recovering, the write failed (paho swallows whatever a callback raises),
    and the kiosk said "card reader not available" with a working reader until the next change,
    which never came.

    The last status is also compared with the row every STATUS_CHECK_SECONDS: a reset, a
    restore or an import replaces the database under this process, and the row it wrote is
    gone or old afterwards - the same "not available" with a working reader."""

    label = "status"

    def __init__(self):
        self.pending = None
        self.known = None
        self.tried_at = 0.0
        self.checked_at = time.monotonic()

    def write(self, value) -> None:
        raise NotImplementedError

    def in_step(self, value) -> bool:
        raise NotImplementedError

    def describe(self, value) -> str:
        return str(value)

    def set(self, value) -> None:
        self.pending = self.known = value
        self.tried_at = 0.0
        self.flush()

    def flush(self) -> None:
        if self.pending is None:
            self._check()
            return
        if time.monotonic() - self.tried_at < STATUS_RETRY_SECONDS:
            return
        self.tried_at = time.monotonic()
        try:
            close_old_connections()  # one that broke with the database's restart is no use
            self.write(self.pending)
        except DatabaseError as exc:
            log.warning("Could not store the %s yet (%s) - retrying", self.label, exc)
            return
        log.info("%s: %s", self.label, self.describe(self.pending))
        self.pending = None

    def _check(self) -> None:
        if self.known is None or time.monotonic() - self.checked_at < STATUS_CHECK_SECONDS:
            return
        self.checked_at = time.monotonic()
        try:
            close_old_connections()
            in_step = self.in_step(self.known)
        except DatabaseError as exc:
            log.warning("Could not check the stored %s (%s)", self.label, exc)
            return
        if not in_step:
            log.info("The stored %s was gone or old (database replaced?) - writing it again", self.label)
            self.set(self.known)


class ReaderStatus(StoredStatus):
    label = "NFC reader status"

    def write(self, value) -> None:
        set_reader_online(value)

    def in_step(self, value) -> bool:
        return NfcReaderStatus.objects.filter(pk=1, is_online=value).exists()

    def describe(self, value) -> str:
        return "online" if value else "offline"


class MachineRow(StoredStatus):
    """The coffee machine bridge's retained topics, merged into the one MachineStatus row."""

    label = "coffee machine status"

    def set_fields(self, **fields) -> None:
        self.set({**(self.known or {}), **fields})

    def write(self, value) -> None:
        machine.store(value)

    def in_step(self, value) -> bool:
        row = MachineStatus.objects.filter(pk=1).first()
        return row is not None and all(getattr(row, k) == v for k, v in value.items())

    def describe(self, value) -> str:
        return ", ".join(sorted(value))


class MachineConfig:
    """The config the bridge runs with is CoffeePy's (dashboard → Coffee machine), retained on the
    broker - which keeps nothing across its restarts (persistence false). So it is sent on every
    connect, and again whenever the database's differs from what was sent: a save whose publish
    didn't get through, a restore, a reset."""

    def __init__(self):
        self.sent = None
        self.checked_at = 0.0

    def push(self, client, force=False) -> None:
        if not force and time.monotonic() - self.checked_at < STATUS_CHECK_SECONDS:
            return
        self.checked_at = time.monotonic()
        try:
            close_old_connections()
            payload = machine.config_payload()
        except DatabaseError as exc:
            log.warning("Could not read the coffee machine config (%s)", exc)
            return
        if force or payload != self.sent:
            client.publish(machine.topic("config"), json.dumps(payload), qos=1, retain=True)
            self.sent = payload
            log.info("coffee machine config sent: %s", payload)


class Command(BaseCommand):
    help = ("Subscribe to the acr122u-mqtt bridge and relay card events into NfcScan rows, and to the "
            "coffee machine bridge (coffee-machine/) and keep MachineStatus.")

    def handle(self, *args, **options):
        client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

        last_prune = time.monotonic()
        status = ReaderStatus()
        machine_row = MachineRow()
        config = MachineConfig()
        machine_prefix = machine.topic("")
        last_housekeeping = 0.0

        def on_connect(c, userdata, flags, rc):
            log.info("Connected to MQTT broker %s:%s (rc=%s)", MQTT_HOST, MQTT_PORT, rc)
            c.subscribe(MQTT_TOPIC)
            c.subscribe(STATUS_TOPIC)
            for name in machine.FROM_BRIDGE:
                c.subscribe(machine.topic(name), qos=1)
            config.push(c, force=True)

        def on_disconnect(c, userdata, rc):
            # Lost the broker ourselves - we can no longer vouch for the bridge's state
            # either, so don't keep showing a possibly-stale "online".
            log.warning("MQTT disconnected (rc=%s) - marking reader status unknown", rc)
            status.set(False)
            machine_row.set_fields(online=False)

        def on_message(c, userdata, msg):
            nonlocal last_prune
            if msg.topic == STATUS_TOPIC:
                status.set(msg.payload.decode("utf-8", errors="replace").strip().lower() == "online")
                return
            if msg.topic.startswith(machine_prefix):
                on_machine(msg.topic[len(machine_prefix):], msg.payload)
                return

            try:
                close_old_connections()
                scan = handle_payload(msg.payload)
            except DatabaseError as exc:
                log.warning("Could not store a card event (%s) - it is lost, the card has to be tapped again", exc)
                return
            if scan is not None:
                log.info("Recorded scan #%s: %s uid=%s", scan.id, scan.event, scan.uid)
            if time.monotonic() - last_prune > 300:
                cutoff = timezone.now() - timezone.timedelta(seconds=SCAN_RETENTION_SECONDS)
                deleted, _ = NfcScan.objects.filter(created_at__lt=cutoff).delete()
                if deleted:
                    log.info("Pruned %s old NfcScan rows", deleted)
                last_prune = time.monotonic()

        def on_machine(name, raw):
            payload = machine.parse(name, raw)
            if payload is None:
                return
            fields = machine.row_fields(name, payload)
            previous_alerts = ((machine_row.known or {}).get("state") or {}).get("alerts")
            if fields is not None:
                machine_row.set_fields(**fields)
            try:
                close_old_connections()
                if name == "scan":
                    machine.handle_scan(payload)
                elif name == "event":
                    machine.handle_event(payload)
                elif name == "products":
                    changes = machine.sync_products(payload)
                    if changes:
                        log.info("coffee sorts synced with the machine: %s", changes)
                        audit.log_actor("coffee machine", "coffee_sort.sync", details="; ".join(
                            f"{k}: {', '.join(v)}" for k, v in changes.items()))
                elif name == "counters":
                    machine.record_counters(payload)
                elif name == "state" and previous_alerts is not None and payload.get("connected"):
                    if set(previous_alerts) != set(payload.get("alerts") or ()):
                        machine.track_alerts(previous_alerts, payload.get("alerts"))
            except DatabaseError as exc:
                # The retained ones come again (products at every connect, counters every 15 min).
                log.warning("Could not store the coffee machine's %s (%s)", name, exc)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        while True:
            try:
                client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
                # A loop of our own rather than loop_forever(): a status the database
                # wouldn't take yet is retried in between.
                while True:
                    rc = client.loop(timeout=1.0)
                    if rc != mqtt.MQTT_ERR_SUCCESS:
                        raise OSError(f"MQTT loop ended (rc={rc})")
                    status.flush()
                    machine_row.flush()
                    config.push(client)
                    if time.monotonic() - last_housekeeping > STATUS_CHECK_SECONDS:
                        last_housekeeping = time.monotonic()
                        try:
                            close_old_connections()
                            machine.housekeeping()
                        except DatabaseError as exc:
                            log.warning("Could not look at the coffee machine's brews (%s)", exc)
            except OSError as exc:
                log.warning("MQTT connection failed: %s - retrying in 5s", exc)
                status.set(False)
                machine_row.set_fields(online=False)
                time.sleep(5)
