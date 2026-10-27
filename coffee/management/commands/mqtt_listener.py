import json
import logging
import os
import time

import paho.mqtt.client as mqtt
from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections
from django.utils import timezone

from coffee.models import NfcReaderStatus, NfcScan

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


class ReaderStatus:
    """The reader status still to be written, kept until the write succeeds. The bridge
    sends it once, retained, and again only when it changes - after a power cut this
    process got it while PostgreSQL was still recovering, the write failed (paho swallows
    whatever a callback raises), and the kiosk said "card reader not available" with a
    working reader until the next change, which never came."""

    def __init__(self):
        self.pending = None
        self.tried_at = 0.0

    def set(self, is_online: bool) -> None:
        self.pending = is_online
        self.tried_at = 0.0
        self.flush()

    def flush(self) -> None:
        if self.pending is None or time.monotonic() - self.tried_at < STATUS_RETRY_SECONDS:
            return
        self.tried_at = time.monotonic()
        try:
            close_old_connections()  # one that broke with the database's restart is no use
            set_reader_online(self.pending)
        except DatabaseError as exc:
            log.warning("Could not store the reader status yet (%s) - retrying", exc)
            return
        log.info("NFC reader status: %s", "online" if self.pending else "offline")
        self.pending = None


class Command(BaseCommand):
    help = "Subscribe to the acr122u-mqtt bridge and relay card events into NfcScan rows."

    def handle(self, *args, **options):
        client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

        last_prune = time.monotonic()
        status = ReaderStatus()

        def on_connect(c, userdata, flags, rc):
            log.info("Connected to MQTT broker %s:%s (rc=%s)", MQTT_HOST, MQTT_PORT, rc)
            c.subscribe(MQTT_TOPIC)
            c.subscribe(STATUS_TOPIC)

        def on_disconnect(c, userdata, rc):
            # Lost the broker ourselves - we can no longer vouch for the bridge's state
            # either, so don't keep showing a possibly-stale "online".
            log.warning("MQTT disconnected (rc=%s) - marking reader status unknown", rc)
            status.set(False)

        def on_message(c, userdata, msg):
            nonlocal last_prune
            if msg.topic == STATUS_TOPIC:
                status.set(msg.payload.decode("utf-8", errors="replace").strip().lower() == "online")
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
            except OSError as exc:
                log.warning("MQTT connection failed: %s - retrying in 5s", exc)
                status.set(False)
                time.sleep(5)
