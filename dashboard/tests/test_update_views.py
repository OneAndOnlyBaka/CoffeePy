import json
import time
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry
from coffee.tests.device import FakeDevice
from dashboard import restore_jobs


class UpdatePageTestCase(FakeDevice, TestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")
        self.updates = self.root / "updates"
        (self.updates / "incoming").mkdir(parents=True)

    def write(self, name, data):
        (self.updates / name).write_text(json.dumps(data))

    def test_page(self):
        self.write("installed.json", {"version": "v2", "applied_at": time.time()})
        self.write("previous.json", {"installed": {"version": "v1"}})
        (self.updates / "pre-update").mkdir()
        (self.updates / "pre-update" / "before-v1.dump").write_bytes(b"x")
        response = self.client.get(reverse("dashboard:update"))
        for text in ("v2", "Back to v1", "before-v1.dump", "No update processed yet."):
            self.assertContains(response, text)

    def test_page_on_a_fresh_device(self):
        response = self.client.get(reverse("dashboard:update"))
        self.assertContains(response, "dev")  # the version of this checkout
        self.assertNotContains(response, "Back to")

    def test_upload(self):
        bundle = SimpleUploadedFile("coffeepy-update-v3.tar", b"TAR")
        self.client.post(reverse("dashboard:update_upload"), {"bundle": bundle, "allow_downgrade": "1"})
        self.assertEqual((self.updates / "incoming" / "coffeepy-update-v3.tar").read_bytes(), b"TAR")
        options = json.loads((self.updates / "incoming" / "coffeepy-update-v3.tar.options.json").read_text())
        self.assertEqual(options, {"allow_downgrade": True, "requested_by": "boss"})
        self.assertEqual(AuditLogEntry.objects.get(action="update.upload").details, "downgrade allowed")
        response = self.client.post(reverse("dashboard:update_upload"),
                                    {"bundle": SimpleUploadedFile("again.tar", b"TAR")}, follow=True)
        self.assertContains(response, "already being applied")  # one waiting is enough

    def test_upload_refused(self):
        response = self.client.post(reverse("dashboard:update_upload"),
                                    {"bundle": SimpleUploadedFile("notes.txt", b"x")}, follow=True)
        self.assertContains(response, "Please upload a .tar update bundle")
        restore_jobs.write_status(state="running")
        response = self.client.post(reverse("dashboard:update_upload"),
                                    {"bundle": SimpleUploadedFile("u.tar", b"x")}, follow=True)
        self.assertContains(response, "A restore or an import is running")
        restore_jobs.write_status(state="idle")
        self.write("state.json", {"state": "running", "updated": time.time()})
        response = self.client.post(reverse("dashboard:update_upload"),
                                    {"bundle": SimpleUploadedFile("u.tar", b"x")}, follow=True)
        self.assertContains(response, "already being applied")

    def test_rollback(self):
        response = self.client.post(reverse("dashboard:update_rollback"), follow=True)
        self.assertContains(response, "no previous version")
        self.write("previous.json", {"installed": {"version": "v1"}})
        self.client.post(reverse("dashboard:update_rollback"))
        self.assertEqual(json.loads((self.updates / "incoming" / "rollback.request").read_text()), {"requested_by": "boss"})
        response = self.client.post(reverse("dashboard:update_rollback"), follow=True)
        self.assertContains(response, "already being applied")

    def test_status(self):
        self.assertEqual(self.client.get(reverse("dashboard:update_status")).json(),
                         {"state": {}, "log": "", "pending": False})
        (self.updates / "status").mkdir()
        (self.updates / "status" / "u.tar.log").write_text("[..] Loading the new images")
        self.write("state.json", {"state": "running", "updated": 0})
        data = self.client.get(reverse("dashboard:update_status")).json()
        self.assertEqual(data["state"]["state"], "stale")  # nothing moved for half an hour: dead
        self.assertIn("Loading", data["log"])

    def test_managers_are_kept_out(self):
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        self.assertRedirects(client.get(reverse("dashboard:update")), reverse("dashboard:index"))
