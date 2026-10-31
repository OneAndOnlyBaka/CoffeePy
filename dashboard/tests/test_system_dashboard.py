import json
import shutil
import tempfile
import time
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee.models import AuditLogEntry


class SystemDashboardTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def test_requires_login(self):
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_non_superuser_is_rejected(self):
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertEqual(response.status_code, 302)

    @override_settings(PUBLIC_HOST="192.168.4.1")
    def test_superuser_sees_cpu_memory_disk_and_public_host(self):
        User.objects.create_superuser(username="root", password="pw")
        self.client.login(username="root", password="pw")
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertContains(response, "CPU")
        self.assertContains(response, "Memory")
        self.assertContains(response, "Disk")
        self.assertContains(response, "192.168.4.1")


class SshTestCase(TestCase):
    """The System page's SSH switch - a request to the host's storage helper, which reports back
    in status.json."""

    def setUp(self):
        self.storage = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.storage)
        override = override_settings(STORAGE_DIR=self.storage)
        override.enable()
        self.addCleanup(override.disable)
        User.objects.create_superuser(username="root", password="pw")
        self.client.login(username="root", password="pw")

    def status(self, **ssh):
        (self.storage / "status.json").write_text(json.dumps({"updated": time.time(), "ssh": ssh}))

    def test_unknown_without_the_helper(self):
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertContains(response, "storage service is not running")
        self.assertNotContains(response, "Switch SSH")

    def test_off(self):
        self.status(active=False, checked=1, error="")
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertContains(response, "Switch SSH on")
        self.assertNotContains(response, "SSH is on")

    def test_on_warns(self):
        self.status(active=True, checked=1, error="systemctl: nope")
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertContains(response, "Switch SSH off")
        self.assertContains(response, "SSH is on")
        self.assertContains(response, "systemctl: nope")

    def test_switching_asks_the_helper_and_is_audited(self):
        for enabled, action in (("1", "ssh.enable"), ("0", "ssh.disable")):
            response = self.client.post(reverse("dashboard:system_dashboard"), {"form": "ssh", "enabled": enabled})
            self.assertRedirects(response, reverse("dashboard:system_dashboard"))
            [request] = (self.storage / "requests").glob("*.json")
            self.assertEqual(json.loads(request.read_text())["enabled"], enabled == "1")
            request.unlink()
            self.assertEqual(AuditLogEntry.objects.latest("id").action, action)

    def test_only_for_admins(self):
        User.objects.create_user(username="manager", password="pw")
        self.client.login(username="manager", password="pw")
        self.client.post(reverse("dashboard:system_dashboard"), {"form": "ssh", "enabled": "1"})
        self.assertFalse((self.storage / "requests").exists())
