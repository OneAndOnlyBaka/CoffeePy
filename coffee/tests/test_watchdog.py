import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from kiosk import views as kiosk_views

SCRIPT = settings.BASE_DIR / "install" / "kiosk_watchdog.sh"


@skipUnless(shutil.which("sh") and shutil.which("awk") and shutil.which("stat"), "needs a POSIX shell")
class WatchdogTestCase(SimpleTestCase):
    """install/kiosk_watchdog.sh with a fake curl (the app's health) and systemctl."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.healthy = self.root / "healthy"
        self.calls = self.root / "calls"
        fakes = {"curl": f'#!/bin/sh\n[ -f "{self.healthy}" ]\n',
                 "systemctl": f'#!/bin/sh\necho "$*" >> "{self.calls}"\n',
                 "logger": "#!/bin/sh\nexit 0\n"}
        for name, body in fakes.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.storage = self.root / "storage"
        self.storage.mkdir()
        self.maintenance = self.root / "status.json"
        self.state = self.root / "state"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "COFFEEPY_STORAGE_DIR": str(self.storage),
                    "COFFEEPY_MAINTENANCE_FILE": str(self.maintenance), "WATCHDOG_STATE_DIR": str(self.state),
                    "WATCHDOG_UPTIME": "3600"}
        self.healthy.touch()

    def run_once(self, **env):
        subprocess.run(["sh", str(SCRIPT)], env={**self.env, **env}, check=True, capture_output=True)
        return self.restarts()

    def restarts(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def events(self):
        path = self.storage / "events.jsonl"
        return [json.loads(line)["action"] for line in path.read_text().splitlines()] if path.exists() else []

    def beat(self, age):
        heartbeat = self.storage / "kiosk-heartbeat"
        heartbeat.touch()
        os.utime(heartbeat, (time.time() - age, time.time() - age))

    def pass_grace(self):
        """Pretends the watchdog's last step was long ago."""
        last = self.state / "last-action"
        if last.exists():
            last.write_text(str(int(last.read_text()) - 3600))

    def test_all_well(self):
        self.beat(10)
        self.assertEqual(self.run_once(), [])
        self.assertEqual(self.events(), [])

    def test_no_kiosk_ever_polled(self):
        self.assertEqual(self.run_once(), [])

    def test_the_app_stops_answering(self):
        self.healthy.unlink()
        self.assertEqual(self.run_once(), [])
        self.assertEqual(self.run_once(), [])  # two minutes: could be a restart of its own
        self.assertEqual(self.run_once(), ["restart coffeepy.service"])
        self.assertEqual(self.events(), ["watchdog.stack_restart"])
        self.assertEqual(self.run_once(), ["restart coffeepy.service"])  # its grace period: waits

    def test_a_kiosk_that_stopped_polling(self):
        self.beat(600)
        self.assertEqual(self.run_once(), ["restart getty@tty1.service"])
        self.pass_grace()
        # Still no poll after the session restart: then the stack.
        self.assertEqual(self.run_once(), ["restart getty@tty1.service", "restart coffeepy.service"])
        self.assertEqual(self.events(), ["watchdog.kiosk_restart", "watchdog.stack_restart"])

    def test_a_kiosk_restart_that_helped(self):
        self.beat(600)
        self.run_once()
        self.pass_grace()
        self.beat(5)
        self.run_once()
        self.beat(600)  # days later, it hangs again: the session first again
        self.pass_grace()
        self.assertEqual(self.run_once()[-1], "restart getty@tty1.service")

    def test_hands_off_while_the_device_is_worked_on(self):
        self.healthy.unlink()
        self.beat(600)
        self.maintenance.write_text("{}")
        for _ in range(5):
            self.assertEqual(self.run_once(), [])
        os.utime(self.maintenance, (time.time() - 3 * 3600,) * 2)  # left behind by a dead process
        self.run_once()
        self.run_once()
        self.assertEqual(self.run_once(), ["restart coffeepy.service"])

    def test_not_right_after_a_boot(self):
        self.healthy.unlink()
        self.beat(600)
        for _ in range(5):
            self.assertEqual(self.run_once(WATCHDOG_UPTIME="120"), [])

    def test_gives_up_after_a_few_restarts_an_hour(self):
        self.beat(600)
        for _ in range(8):
            self.run_once()
            self.pass_grace()
        self.assertEqual(len(self.restarts()), 3)
        self.assertEqual(self.events().count("watchdog.gave_up"), 1)  # said once, not every minute


class HeartbeatTestCase(SimpleTestCase):
    def test_touched_at_most_every_thirty_seconds(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        heartbeat = root / "kiosk-heartbeat"
        with override_settings(STORAGE_DIR=root):
            kiosk_views._heartbeat_at = 0.0
            kiosk_views._heartbeat()
            self.assertTrue(heartbeat.exists())
            os.utime(heartbeat, (0, 0))
            kiosk_views._heartbeat()  # within the 30s: not again
            self.assertEqual(heartbeat.stat().st_mtime, 0)
            kiosk_views._heartbeat_at -= kiosk_views.HEARTBEAT_SECONDS
            kiosk_views._heartbeat()
            self.assertGreater(heartbeat.stat().st_mtime, 0)
