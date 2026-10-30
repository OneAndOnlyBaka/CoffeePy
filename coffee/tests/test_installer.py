import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = settings.BASE_DIR / "install" / "install.sh"


@skipUnless(shutil.which("bash"), "needs bash")
class InstallerTestCase(SimpleTestCase):
    """install/install.sh without a Pi: --dry-run, or sourced for single functions."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("SUDO_USER", "KIOSK_USER", "COFFEEPY_FROM_WATCHER", "VIDEO_MODE")}

    def sourced(self, code, **env):
        result = subprocess.run(["bash", "-c", f'. "{SCRIPT}"; {code}'], env={**self.env, **env},
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def dry_run(self, *args, **env):
        return subprocess.run(["bash", str(SCRIPT), "--dry-run", *args], env={**self.env, **env},
                              capture_output=True, text=True)

    # The update watcher is root's own service: no SUDO_USER, and no "pi" on a current image.
    def test_kiosk_user_comes_from_the_installed_autologin(self):
        dropin = self.root / "autologin.conf"
        dropin.write_text((settings.BASE_DIR / "install" / "kiosk" / "getty-autologin.conf").read_text()
                          .replace("__FIRST_USER_NAME__", "coffeepy"))
        ask = f'GETTY_AUTOLOGIN="{dropin}"; kiosk_user'
        self.assertEqual(self.sourced(ask), "coffeepy")
        self.assertEqual(self.sourced(ask, SUDO_USER="admin"), "admin")
        self.assertEqual(self.sourced(ask, SUDO_USER="admin", KIOSK_USER="kiosk"), "kiosk")
        self.assertEqual(self.sourced(f'GETTY_AUTOLOGIN="{self.root}/missing"; kiosk_user'), "pi")

    def fake_ip(self, output):
        """A PATH with an `ip` that answers `ip -4 route get` like this host would."""
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "ip").write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
        (bin_dir / "ip").chmod(0o755)
        return f"{bin_dir}:{self.env['PATH']}"

    # Without the AP, PUBLIC_HOST is the LAN address - the app's own guess, from inside its
    # container, would be the container's.
    def test_the_address_phones_reach_without_the_ap(self):
        route = "1.1.1.1 via 192.168.10.1 dev eth0 src 192.168.10.28 uid 1000"
        self.assertEqual(self.sourced("host_address", PATH=self.fake_ip(route)), "192.168.10.28")
        self.assertEqual(self.sourced("host_address", PATH=self.fake_ip("")), "")  # no route at all

    def test_a_misspelt_step_is_refused(self):
        result = self.dry_run("--only=hardwar")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown step in --only: hardwar", result.stderr)

    def test_only_takes_a_list_and_keeps_the_usual_order(self):
        result = self.dry_run("--only=services,hardware")
        steps = re.findall(r"\x1b\[0m Step (\w+)$", result.stdout, re.MULTILINE)
        self.assertEqual(steps, ["hardware", "services"])

    def test_an_update_leaves_the_display_mode_alone(self):
        self.assertIn("Pinning the display mode", self.dry_run("--only=hardware").stdout)
        result = self.dry_run("--only=hardware", COFFEEPY_FROM_WATCHER="1")
        self.assertNotIn("Pinning the display mode", result.stdout)
        self.assertNotIn("video=", result.stdout)

    def test_the_maintenance_status_is_the_watchers_during_an_update(self):
        status = self.root / "data" / "maintenance" / "status.json"
        self.sourced(f'TARGET_DIR="{self.root}"; start_maintenance; test -f "{status}"; trap - EXIT')
        status.unlink()
        self.sourced(f'TARGET_DIR="{self.root}"; start_maintenance; test ! -e "{status}"',
                     COFFEEPY_FROM_WATCHER="1")
        # And without the watcher, the installer's own status goes again when it exits.
        self.sourced(f'TARGET_DIR="{self.root}"; start_maintenance')
        self.assertFalse(status.exists())

    def test_ssh_is_switched_off_unless_kept(self):
        result = self.dry_run("--only=ssh")
        self.assertIn("[dry-run] systemctl disable --now ssh.service", result.stdout)
        self.assertIn("[dry-run] systemctl disable --now ssh.socket", result.stdout)
        result = self.dry_run("--only=ssh", "--keep-ssh")
        self.assertIn("Leaving SSH as it is", result.stdout)
        self.assertNotIn("systemctl disable", result.stdout)
        # The update watcher's run never reaches it.
        self.assertNotIn("Step ssh", self.dry_run("--only=hardware,services,kiosk").stdout)
