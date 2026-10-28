import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from coffee.models import AuditLogEntry, NfcScan
from dashboard import night
from dashboard.models import NightPolicy

SCRIPT = settings.BASE_DIR / "install" / "kiosk_night.py"
_spec = importlib.util.spec_from_file_location("kiosk_night", SCRIPT)
kiosk_night = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kiosk_night)


WEEKDAYS = {"enabled": True, "off": "20:00", "on": "05:30", "weekend": "same", "weekend_off": "20:00",
            "weekend_on": "08:00"}


def at(text):
    """2026-11-02 is a Monday."""
    return datetime.fromisoformat(text)


class IsNightTestCase(SimpleTestCase):
    def night(self, when, **changes):
        return night.is_night(at(when), {**WEEKDAYS, **changes})

    def test_across_midnight(self):
        self.assertTrue(self.night("2026-11-02T20:00"))
        self.assertTrue(self.night("2026-11-03T02:00"))
        self.assertFalse(self.night("2026-11-03T05:30"))
        self.assertFalse(self.night("2026-11-03T12:00"))

    def test_within_one_day(self):
        self.assertTrue(self.night("2026-11-03T03:59", off="01:00", on="04:00"))
        self.assertFalse(self.night("2026-11-03T23:00", off="01:00", on="04:00"))

    def test_off_or_equal_times_is_never_night(self):
        self.assertFalse(self.night("2026-11-02T22:00", enabled=False))
        self.assertFalse(self.night("2026-11-02T20:00", off="20:00", on="20:00"))

    def test_a_weekend_off_all_day(self):
        self.assertFalse(self.night("2026-11-06T19:59", weekend="all_day"))  # Friday
        self.assertTrue(self.night("2026-11-06T20:00", weekend="all_day"))
        self.assertTrue(self.night("2026-11-07T12:00", weekend="all_day"))  # Saturday
        self.assertTrue(self.night("2026-11-08T23:59", weekend="all_day"))  # Sunday
        self.assertTrue(self.night("2026-11-09T05:29", weekend="all_day"))  # Monday morning
        self.assertFalse(self.night("2026-11-09T05:30", weekend="all_day"))

    def test_weekend_times_of_its_own(self):
        rule = {"weekend": "times", "weekend_off": "18:00", "weekend_on": "09:00"}
        self.assertFalse(self.night("2026-11-07T07:00", **rule))  # Friday's night ended at 05:30
        self.assertTrue(self.night("2026-11-07T18:00", **rule))
        self.assertTrue(self.night("2026-11-08T08:59", **rule))  # Saturday's night into Sunday
        self.assertFalse(self.night("2026-11-08T09:00", **rule))
        self.assertTrue(self.night("2026-11-09T08:59", **rule))  # Sunday's into Monday
        self.assertTrue(self.night("2026-11-09T20:00", **rule))
        self.assertFalse(self.night("2026-11-08T12:00", weekend="times", weekend_off="09:00", weekend_on="09:00"))

    def test_the_host_script_decides_the_same(self):
        for changes in ({}, {"off": "01:00", "on": "04:00"}, {"off": "08:00", "on": "08:00"}, {"weekend": "all_day"},
                        {"weekend": "times", "weekend_off": "18:00", "weekend_on": "09:00"}):
            schedule = {**WEEKDAYS, **changes}
            for day in range(2, 10):
                for hour in range(24):
                    for minute in (0, 29, 30):
                        now = datetime(2026, 11, day, hour, minute)
                        self.assertEqual(kiosk_night.is_night(now, schedule), night.is_night(now, schedule))


class HostScriptTestCase(SimpleTestCase):
    """install/kiosk_night.py as the kiosk session and the watchdog run it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.file = self.root / "night.json"

    def night_at(self, now=None):
        result = subprocess.run([sys.executable, str(SCRIPT), "--file", str(self.file)]
                                + (["--now", now] if now else []))
        return result.returncode == 0

    def test_follows_the_file(self):
        self.file.write_text(json.dumps(WEEKDAYS))
        self.assertTrue(self.night_at("2026-11-02T23:00"))
        self.assertFalse(self.night_at("2026-11-03T07:00"))
        self.file.write_text(json.dumps({**WEEKDAYS, "enabled": False}))
        self.assertFalse(self.night_at("2026-11-02T23:00"))

    def test_now_is_the_dashboards_time_zone(self):
        # A night from 00:00 to 23:59 in a zone 14 hours off: whatever the host's own zone, the
        # script must go by the file's.
        for tz in ("Pacific/Kiritimati", "UTC"):
            self.file.write_text(json.dumps({**WEEKDAYS, "off": "00:00", "on": "23:59", "tz": tz}))
            local = datetime.now(ZoneInfo(tz)).time()
            self.assertEqual(self.night_at(), local < time(23, 59), tz)

    def test_anything_broken_is_day(self):
        self.assertFalse(self.night_at("2026-11-02T23:00"))  # no file
        for content in ("", "{", '{"enabled": true}', json.dumps({**WEEKDAYS, "enabled": "yes"}),
                        json.dumps({**WEEKDAYS, "off": "8pm"}), json.dumps({**WEEKDAYS, "tz": "Mars/Olympus"}), "[]"):
            with self.subTest(content=content):
                self.file.write_text(content)
                self.assertFalse(self.night_at("2026-11-02T23:00"))


class NightSettingTestCase(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        storage = override_settings(STORAGE_DIR=self.root)
        storage.enable()
        self.addCleanup(storage.disable)
        User.objects.create_superuser(username="root", password="pw")
        self.client = Client()
        self.client.login(username="root", password="pw")

    def schedule(self):
        return json.loads((self.root / "night.json").read_text())

    def test_off_by_default(self):
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertContains(response, 'value="20:00"')
        self.assertFalse(NightPolicy.current().enabled)

    def test_saving_writes_the_schedule_and_an_audit_row(self):
        response = self.client.post(reverse("dashboard:system_dashboard"),
                                    {"enabled": "on", "off_at": "21:15", "on_at": "06:00", "weekend": "same",
                                     "weekend_off_at": "20:00", "weekend_on_at": "08:00"})
        self.assertRedirects(response, reverse("dashboard:system_dashboard"))
        self.assertEqual(self.schedule(), {"enabled": True, "off": "21:15", "on": "06:00", "weekend": "same",
                                           "weekend_off": "20:00", "weekend_on": "08:00", "tz": settings.TIME_ZONE})
        self.assertTrue(AuditLogEntry.objects.filter(action="night.update", details="21:15-06:00").exists())
        self.client.post(reverse("dashboard:system_dashboard"),
                         {"enabled": "on", "off_at": "21:15", "on_at": "06:00", "weekend": "all_day",
                          "weekend_off_at": "20:00", "weekend_on_at": "08:00"})
        self.assertEqual(self.schedule()["weekend"], "all_day")
        self.assertTrue(AuditLogEntry.objects.filter(action="night.update",
                                                     details="21:15-06:00, weekend all_day").exists())
        self.client.post(reverse("dashboard:system_dashboard"), {"off_at": "21:15", "on_at": "06:00", "weekend": "same",
                                                                 "weekend_off_at": "20:00", "weekend_on_at": "08:00"})
        self.assertFalse(self.schedule()["enabled"])
        self.assertTrue(AuditLogEntry.objects.filter(action="night.update", details="off").exists())

    def test_equal_weekend_times_are_refused(self):
        response = self.client.post(reverse("dashboard:system_dashboard"),
                                    {"enabled": "on", "off_at": "20:00", "on_at": "05:30", "weekend": "times",
                                     "weekend_off_at": "09:00", "weekend_on_at": "09:00"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NightPolicy.current().enabled)

    def test_equal_times_are_refused(self):
        response = self.client.post(reverse("dashboard:system_dashboard"),
                                    {"enabled": "on", "off_at": "20:00", "on_at": "20:00", "weekend": "same",
                                     "weekend_off_at": "20:00", "weekend_on_at": "08:00"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NightPolicy.current().enabled)
        self.assertFalse((self.root / "night.json").exists())

    def test_tick_brings_the_file_back(self):
        NightPolicy.objects.update_or_create(pk=1, defaults={"enabled": True})
        with mock.patch("coffee.clock.check", return_value=None), \
                mock.patch("coffee.management.commands.tick.call_command"):
            call_command("tick", stdout=mock.Mock(), stderr=mock.Mock())
        self.assertTrue(self.schedule()["enabled"])
        self.assertFalse(night.write_file())  # unchanged: not rewritten


class KioskMorningTestCase(TestCase):
    def test_a_reloaded_kiosk_ignores_the_taps_of_the_night(self):
        client = Client()
        client.get(reverse("kiosk:index"))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        newest = NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        client.get(reverse("kiosk:index"))  # the browser of the morning, same session cookie
        self.assertEqual(client.session["last_scan_id"], newest.id)
