import json
import os
import shutil
import subprocess
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from coffee import rtc
from coffee.models import CoffeeSort
from coffee.tests.device import FakeDevice

SCRIPT = settings.BASE_DIR / "install" / "rtc_sync.sh"
# What a fake hwclock does: --show prints the RTC file (or fails when it says "fail"); --systohc
# notes the call and, unless told to fail, sets the RTC to the system clock.
FAKE_HWCLOCK = """#!/bin/sh
case "$1" in
--show) [ "$(cat "$FAKE_RTC")" = fail ] && exit 1; cat "$FAKE_RTC" ;;
--systohc) echo systohc >> "$FAKE_LOG"; [ -n "${FAKE_SYSTOHC_FAILS:-}" ] && exit 1; date -Iseconds > "$FAKE_RTC" ;;
esac
"""


def status(state, age=0, **fields):
    return {"state": state, "checked": time.time() - age, **fields}


class RtcHealthTestCase(FakeDevice, TestCase):
    def write(self, data):
        (self.storage / "rtc.json").write_text(data if isinstance(data, str) else json.dumps(data))

    def test_states(self):
        self.assertEqual(rtc.health(), {"state": "unknown", "disturbed": False})  # no check ran yet
        for data, state, disturbed in ((status("ok"), "ok", False), (status("missing"), "missing", True),
                                       (status("unreadable"), "unreadable", True), (status("lost"), "lost", True),
                                       (status("something new"), "ok", False),
                                       (status("missing", age=7200), "unknown", False),  # the timer stopped
                                       ({"state": "missing", "checked": "yesterday"}, "unknown", False),
                                       ("not json", "unknown", False), ([], "unknown", False)):
            with self.subTest(data=data):
                self.write(data)
                health = rtc.health()
                self.assertEqual((health["state"], health["disturbed"]), (state, disturbed))

    def panel(self):
        client = Client()
        client.get(reverse("kiosk:index"))
        return client.get(reverse("kiosk:panel"))

    def test_kiosk_warning_sign(self):
        CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        sign = "The clock module (RTC) is faulty - see the dashboard"
        self.assertNotContains(self.panel(), sign)
        self.write(status("ok"))
        self.assertNotContains(self.panel(), sign)
        self.write(status("lost"))
        response = self.panel()
        self.assertContains(response, sign)
        self.assertContains(response, "device-alarms")
        self.assertNotContains(response, "Backups are not running")  # only what is broken

    def test_dashboard_overview(self):
        User.objects.create_superuser(username="boss", password="pw")
        client = Client()
        client.login(username="boss", password="pw")
        self.assertNotContains(client.get(reverse("dashboard:index")), "Clock module (RTC)")  # nothing known
        for state, text in (("ok", "Keeps the time while the power is off."), ("missing", "Not found"),
                            ("unreadable", "Does not answer"), ("lost", "battery (CR2032) is flat")):
            with self.subTest(state=state):
                self.write(status(state))
                response = client.get(reverse("dashboard:index"))
                self.assertContains(response, "Clock module (RTC)")
                self.assertContains(response, text)
                self.assertContains(response, "faulty" if state != "ok" else "● OK")


@skipUnless(shutil.which("sh") and shutil.which("date"), "needs a POSIX shell")
class RtcSyncScriptTestCase(SimpleTestCase):
    """install/rtc_sync.sh itself, with a fake hwclock and /dev/rtc0."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in (("hwclock", FAKE_HWCLOCK), ("logger", "#!/bin/sh\nexit 0\n")):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.device = self.root / "rtc0"
        self.device.touch()
        self.rtc = self.root / "rtc-time"
        self.storage = self.root / "storage"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_RTC": str(self.rtc),
                    "FAKE_LOG": str(self.root / "calls"), "COFFEEPY_STORAGE_DIR": str(self.storage),
                    "RTC_DEVICE": str(self.device), "BOOT_ID": "boot-1"}

    def run_check(self, rtc_time=None, **env):
        if rtc_time is not None:
            self.rtc.write_text(rtc_time)
        subprocess.run(["sh", str(SCRIPT)], env={**self.env, **env}, check=True, capture_output=True)
        return json.loads((self.storage / "rtc.json").read_text())

    def events(self):
        path = self.storage / "events.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [(e["action"], e["outcome"]) for e in map(json.loads, lines)]

    def systohc_calls(self):
        calls = self.root / "calls"
        return len(calls.read_text().splitlines()) if calls.exists() else 0

    def now(self, offset=0):
        return subprocess.run(["date", "-Iseconds", "-d", f"@{int(time.time()) + offset}"], capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_a_good_rtc(self):
        self.assertEqual(self.run_check(self.now())["state"], "ok")
        self.assertEqual(self.run_check()["state"], "ok")
        self.assertEqual(self.events(), [("rtc.ok", "success")])  # a change is logged, not every check
        self.assertEqual(self.systohc_calls(), 0)

    def test_a_corrected_system_clock_is_written_back(self):
        self.assertEqual(self.run_check(self.now(-3600))["state"], "ok")
        self.assertEqual(self.systohc_calls(), 1)

    def test_no_rtc(self):
        self.device.unlink()
        result = self.run_check()
        self.assertEqual(result["state"], "missing")
        self.assertEqual(self.events(), [("rtc.missing", "failure")])

    def test_an_rtc_that_does_not_answer(self):
        result = self.run_check("fail", FAKE_SYSTOHC_FAILS="1")
        self.assertEqual(result["state"], "unreadable")
        self.assertEqual(self.run_check("not a date")["state"], "unreadable")

    def test_a_stopped_oscillator_is_restarted(self):
        self.assertEqual(self.run_check("fail")["state"], "lost")
        self.assertEqual(self.systohc_calls(), 1)

    def test_lost_time_stays_until_kept_through_a_reboot(self):
        result = self.run_check("2000-01-01 00:00:05+00:00")  # what a DS3231 says after losing power
        self.assertEqual((result["state"], result["lost_boot"]), ("lost", "boot-1"))
        self.assertEqual(self.systohc_calls(), 1)  # set from the system clock right away
        self.assertEqual(self.run_check()["state"], "lost")  # fine now, but not yet through a power cut
        result = self.run_check(BOOT_ID="boot-2")
        self.assertEqual((result["state"], result["lost_boot"]), ("ok", ""))
        self.assertEqual(self.events(), [("rtc.lost", "failure"), ("rtc.ok", "success")])
