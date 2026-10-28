import json
import time
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from coffee import clock
from coffee.models import AuditLogEntry, CoffeeSort
from coffee.tests.device import FakeDevice


class ClockCheckTestCase(FakeDevice, TestCase):
    """coffee/clock.py: a clock that went back is put forward - never back - and shown until confirmed."""

    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.boottime = 1000.0
        self.boot_id = "boot-1"
        self.set_epoch = mock.Mock(side_effect=self.clock_set)
        for target, value in (("coffee.clock.time.time", lambda: self.now),
                              ("coffee.clock._boot", lambda: (self.boot_id, self.boottime)),
                              ("dashboard.system_clock.set_system_epoch", self.set_epoch)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.rtc_status()

    def clock_set(self, epoch):
        self.now = epoch

    def rtc_status(self, ntp="no", **fields):
        (self.storage / "rtc.json").write_text(json.dumps({"state": "ok", "checked": time.time(), "ntp": ntp, **fields}))

    def entry(self, action="order.create", at=None):
        row = AuditLogEntry.objects.create(action=action, actor="Anna")
        AuditLogEntry.objects.filter(pk=row.pk).update(created_at=datetime.fromtimestamp(at or self.now, tz=dt_timezone.utc))
        return row

    def later(self, seconds, wall=None):
        """The device runs on for `seconds`; its wall clock goes to `wall` (default: along with it)."""
        self.boottime += seconds
        self.now = wall if wall is not None else self.now + seconds

    def test_nothing_to_go_by_off_a_device(self):
        (self.storage / "rtc.json").unlink()
        self.entry(at=self.now + 36000)
        self.assertIsNone(clock.check())
        self.set_epoch.assert_not_called()

    def test_a_clock_behind_the_newest_booking_goes_forward(self):
        # The legacy device: bookings until 10:58, then the clock stood at 01:02.
        newest = self.now
        self.entry(at=newest)
        self.now -= 10 * 3600
        correction = clock.check()
        self.assertEqual(correction["reason"], "behind_log")
        self.set_epoch.assert_called_once()
        # The database keeps microseconds, not the float's last digits.
        self.assertAlmostEqual(self.set_epoch.call_args.args[0], newest + clock.AHEAD_OF_ENTRY, places=4)
        entry = AuditLogEntry.objects.get(action="system_time.corrected")
        self.assertEqual(entry.outcome, AuditLogEntry.OUTCOME_FAILURE)
        self.assertIn("behind the newest log entry", entry.details)
        shown = clock.corrected()
        self.assertAlmostEqual(shown["to"].timestamp(), newest + 60, places=4)
        self.assertIsNone(clock.check())  # put right: nothing more to do
        self.assertEqual(self.set_epoch.call_count, 1)

    def test_a_few_minutes_behind_is_left_alone(self):
        self.entry(at=self.now + 200)  # within BEHIND_SECONDS: drift, a host event's own time
        self.assertIsNone(clock.check())

    def test_a_clock_that_went_back_within_one_boot(self):
        clock.check()
        self.later(600, wall=self.now - 3600)  # ten minutes on, the clock says an hour earlier
        expected = self.now + 3600 + 600
        correction = clock.check()
        self.assertEqual(correction["reason"], "went_back")
        self.set_epoch.assert_called_once_with(expected)

    def test_forward_jumps_and_a_new_boot_are_not_corrected(self):
        clock.check()
        self.later(600, wall=self.now + 86400)  # forward: can't tell a right clock from a wrong one
        self.assertIsNone(clock.check())
        self.boot_id, self.boottime = "boot-2", 30.0  # power cut: nothing to compare with
        self.now -= 7200
        self.assertIsNone(clock.check())
        self.set_epoch.assert_not_called()

    def test_ntp_is_in_charge_when_it_set_the_clock(self):
        self.rtc_status(ntp="yes")
        self.entry(at=self.now + 36000)
        clock.check()
        self.later(600, wall=self.now - 3600)
        self.assertIsNone(clock.check())
        self.set_epoch.assert_not_called()

    def test_a_clock_set_back_on_purpose_stays_back(self):
        self.entry(at=self.now + 36000)  # booked while the clock was ten hours ahead
        self.entry(action="system_time.set")  # then an admin put it right
        self.entry(at=self.now)
        self.assertIsNone(clock.check())
        self.set_epoch.assert_not_called()

    def test_confirming_clears_the_warning_and_starts_afresh(self):
        self.entry(at=self.now + 36000)
        clock.check()
        self.assertIsNotNone(clock.corrected())
        clock.confirmed()
        self.assertIsNone(clock.corrected())

    def test_tick_runs_the_check_before_the_backup(self):
        calls = []
        with mock.patch("coffee.clock.check", side_effect=lambda: calls.append("clock")), \
                mock.patch("coffee.management.commands.backup_db.Command.handle", side_effect=lambda *a, **k: calls.append("backup")):
            call_command("tick", stdout=mock.Mock())
        self.assertEqual(calls, ["clock", "backup"])

    def test_a_failing_check_does_not_stop_the_backup(self):
        with mock.patch("coffee.clock.check", side_effect=RuntimeError("no CAP_SYS_TIME")), \
                mock.patch("coffee.management.commands.backup_db.Command.handle") as backup:
            call_command("tick", stdout=mock.Mock(), stderr=mock.Mock())
        backup.assert_called_once()


class ClockScreensTestCase(FakeDevice, TestCase):
    def setUp(self):
        super().setUp()
        moved = time.time() - 60
        (self.storage / "clock.json").write_text(json.dumps(
            {"corrected": {"from": moved - 36000, "to": moved, "reason": "behind_log"}}))
        (self.storage / "rtc.json").write_text(json.dumps({"state": "ok", "checked": time.time(), "ntp": "no"}))
        User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")

    def test_kiosk_sign(self):
        CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        kiosk = Client()
        kiosk.get(reverse("kiosk:index"))
        sign = "The clock was wrong and has been put forward - see the dashboard"
        self.assertContains(kiosk.get(reverse("kiosk:panel")), sign)
        clock.confirmed()
        self.assertNotContains(kiosk.get(reverse("kiosk:panel")), sign)

    def test_overview_and_confirming(self):
        self.assertContains(self.client.get(reverse("dashboard:index")), "before the newest bookings")
        page = self.client.get(reverse("dashboard:system_time"))
        self.assertContains(page, "The time is right")
        self.client.post(reverse("dashboard:system_time"), {"confirm": "1"})
        self.assertIsNone(clock.corrected())
        self.assertTrue(AuditLogEntry.objects.filter(action="system_time.confirmed").exists())
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "before the newest bookings")

    def test_setting_the_time_is_local_time_and_clears_the_warning(self):
        # The form sends the device's local time; the container runs in UTC - `date` gets a Unix time.
        with mock.patch("dashboard.system_clock.subprocess.run") as run:
            self.client.post(reverse("dashboard:system_time"), {"datetime": "2026-10-27T18:00"})
        expected = datetime(2026, 10, 27, 18, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        self.assertEqual(run.call_args_list[0].args[0], ["date", "-s", f"@{int(expected)}"])
        self.assertIsNone(clock.corrected())
        self.assertTrue(AuditLogEntry.objects.filter(action="system_time.set").exists())
