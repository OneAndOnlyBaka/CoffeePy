import json

from django.test import TestCase

from coffee.management.commands.mqtt_listener import handle_payload, set_reader_online
from coffee.models import NfcReaderStatus, NfcScan


class HandlePayloadTestCase(TestCase):
    def test_card_inserted_creates_scan(self):
        payload = json.dumps(
            {"event": "card_inserted", "uid": "A1B2C3D4", "atr": "3B8F...", "card_type": "MIFARE Classic 1K"}
        ).encode()
        scan = handle_payload(payload)
        self.assertIsNotNone(scan)
        self.assertEqual(scan.event, NfcScan.EVENT_INSERTED)
        self.assertEqual(scan.uid, "A1B2C3D4")
        self.assertEqual(NfcScan.objects.count(), 1)

    def test_card_removed_creates_scan(self):
        payload = json.dumps({"event": "card_removed", "reader": "ACS ACR122U"}).encode()
        scan = handle_payload(payload)
        self.assertIsNotNone(scan)
        self.assertEqual(scan.event, NfcScan.EVENT_REMOVED)
        self.assertIsNone(scan.uid)

    def test_retained_status_message_is_ignored(self):
        # The bridge also publishes a plain "online"/"offline" retained status message on
        # <topic>/status - not JSON, and has no "event" key even if it were.
        self.assertIsNone(handle_payload(b"online"))
        self.assertEqual(NfcScan.objects.count(), 0)

    def test_unknown_event_is_ignored(self):
        payload = json.dumps({"event": "something_else"}).encode()
        self.assertIsNone(handle_payload(payload))
        self.assertEqual(NfcScan.objects.count(), 0)

    def test_uid_as_int_matches_legacy_little_endian_conversion(self):
        # Legacy CardScanner.UIDConverter.ToInt([0x04, 0x11, 0x22, 0x33]) == 0x33221104
        payload = json.dumps({"event": "card_inserted", "uid": "04112233"}).encode()
        scan = handle_payload(payload)
        self.assertEqual(scan.uid_as_int(), 0x33221104)


class ReaderStatusTestCase(TestCase):
    def test_defaults_to_offline_when_never_reported(self):
        self.assertFalse(NfcReaderStatus.objects.filter(pk=1, is_online=True).exists())

    def test_set_reader_online_creates_and_updates_the_singleton_row(self):
        set_reader_online(True)
        self.assertTrue(NfcReaderStatus.objects.get(pk=1).is_online)

        set_reader_online(False)
        self.assertEqual(NfcReaderStatus.objects.count(), 1)
        self.assertFalse(NfcReaderStatus.objects.get(pk=1).is_online)


class ReaderStatusRetryTestCase(TestCase):
    """The status the bridge sends once is kept until the database takes it - after a power
    cut the database was still recovering when it came."""

    def test_a_failed_write_is_retried_until_it_works(self):
        from unittest import mock

        from django.db import OperationalError

        from coffee.management.commands import mqtt_listener

        status = mqtt_listener.ReaderStatus()
        with mock.patch.object(mqtt_listener, "set_reader_online",
                               side_effect=OperationalError("the database system is starting up")):
            status.set(True)
        self.assertFalse(NfcReaderStatus.objects.filter(is_online=True).exists())
        self.assertTrue(status.pending)

        status.flush()  # too soon: waits STATUS_RETRY_SECONDS between tries
        self.assertFalse(NfcReaderStatus.objects.filter(is_online=True).exists())

        status.tried_at -= mqtt_listener.STATUS_RETRY_SECONDS
        status.flush()
        self.assertTrue(NfcReaderStatus.objects.get(pk=1).is_online)
        self.assertIsNone(status.pending)
