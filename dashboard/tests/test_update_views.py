import json
import time
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry
from coffee.tests.device import FakeDevice
from dashboard import restore_jobs, versions


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
        self.write("previous.json", {"installed": {"version": "v1"}})  # an older watcher's, mid-update
        (self.updates / "pre-update").mkdir()
        (self.updates / "pre-update" / "before-v1.dump").write_bytes(b"x")  # an older watcher's
        (self.updates / "pre-update" / "before-v2.sql.gz").write_bytes(b"x")
        response = self.client.get(reverse("dashboard:update"))
        for text in ("v2", "before-v1.dump", "before-v2.sql.gz", "No update processed yet."):
            self.assertContains(response, text)
        self.assertNotContains(response, "Back to")  # only ever forward
        self.assertNotContains(response, "allow_downgrade")

    def test_page_on_a_fresh_device(self):
        response = self.client.get(reverse("dashboard:update"))
        self.assertContains(response, settings.COFFEEPY_VERSION)  # the version of this checkout
        self.assertNotContains(response, "Back to")

    def test_upload(self):
        bundle = SimpleUploadedFile("coffeepy-update-v3.tar", b"TAR")
        # An old form's downgrade box is ignored: an older version only gets in as the watcher allows.
        self.client.post(reverse("dashboard:update_upload"), {"bundle": bundle, "allow_downgrade": "1"})
        self.assertEqual((self.updates / "incoming" / "coffeepy-update-v3.tar").read_bytes(), b"TAR")
        options = json.loads((self.updates / "incoming" / "coffeepy-update-v3.tar.options.json").read_text())
        self.assertEqual(options, {"requested_by": "boss"})
        self.assertEqual(AuditLogEntry.objects.get(action="update.upload").details, "")
        response = self.client.post(reverse("dashboard:update_upload"),
                                    {"bundle": SimpleUploadedFile("again.tar", b"TAR")}, follow=True)
        self.assertContains(response, "already being applied")  # one waiting is enough

    def test_no_upload_while_an_interrupted_update_waits(self):
        self.write("journal.json", {"bundle": "coffeepy-update-v2.tar", "phase": "switched"})
        response = self.client.post(reverse("dashboard:update_upload"),
                                    {"bundle": SimpleUploadedFile("u.tar", b"x")}, follow=True)
        self.assertContains(response, "already being applied")
        self.assertFalse((self.updates / "incoming" / "u.tar").exists())

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

    def test_status(self):
        self.assertEqual(self.client.get(reverse("dashboard:update_status")).json(),
                         {"state": {}, "log": "", "pending": False})
        (self.updates / "status").mkdir()
        (self.updates / "status" / "u.tar.log").write_text("[..] Loading the new images")
        self.write("state.json", {"state": "running", "updated": 0})
        data = self.client.get(reverse("dashboard:update_status")).json()
        self.assertEqual(data["state"]["state"], "stale")  # nothing moved for half an hour: dead
        self.assertIn("Loading", data["log"])

    def test_the_installed_packages_for_an_os_bundle(self):
        self.assertNotContains(self.client.get(reverse("dashboard:update")), "Download the installed packages")
        self.assertEqual(self.client.get(reverse("dashboard:update_packages")).status_code, 404)
        (self.updates / "os-packages.txt").write_text("bash\nzlib1g\n")
        self.assertContains(self.client.get(reverse("dashboard:update")), "Download the installed packages")
        response = self.client.get(reverse("dashboard:update_packages"))
        self.assertEqual(b"".join(response.streaming_content), b"bash\nzlib1g\n")
        self.assertIn("coffeepy-os-packages.txt", response["Content-Disposition"])

    def test_managers_are_kept_out(self):
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        self.assertRedirects(client.get(reverse("dashboard:update")), reverse("dashboard:index"))


class VersionListTestCase(FakeDevice, TestCase):
    """The update page's "Versions": app, container and host, and all of it as text to copy."""

    SYSTEM = {
        "updated": 1793013212, "model": "Raspberry Pi 3 Model B Rev 1.2", "os": "Raspbian GNU/Linux 13 (trixie)",
        "kernel": "6.12.47+rpt-rpi-v8", "platform": "linux/arm64", "docker": "26.1.5", "compose": "5.5.0",
        "signature": {"check": True, "key": "bundled", "fingerprint": "0123456789abcdef"},
        "containers": [{"service": "web", "image": "coffeepy-web:latest", "image_id": "0123456789ab", "state": "running"},
                       {"service": "migrate", "image": "coffeepy-web:latest", "image_id": "0123456789ab",
                        "state": "exited"}],
    }

    def setUp(self):
        super().setUp()
        User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")
        self.updates = self.root / "updates"
        self.updates.mkdir(parents=True, exist_ok=True)

    def page(self, system=None, build=None):
        if system is not None:
            (self.updates / "system.json").write_text(json.dumps(system))
        with self.settings(COFFEEPY_BUILD=build or {}, COFFEEPY_VERSION=(build or {}).get("version", "dev")):
            return self.client.get(reverse("dashboard:update"))

    def test_everything_listed_and_copyable(self):
        (self.updates / "installed.json").write_text(json.dumps({"version": "v2.1", "applied_at": 1793013212}))
        system = {**self.SYSTEM, "os_bundle": {"version": "os-trixie-20261030-1200", "applied_at": 1793013212}}
        response = self.page(system, {"version": "0be2def-dirty", "commit": "0be2def64ad5", "branch": "main"})
        text = response.context["version_text"]
        for expected in ("[CoffeePy]", "Version: 0be2def-dirty", "Commit: 0be2def64ad5", "Branch: main",
                         "Installed by update: v2.1 (applied 2026-", "Python: ", "Django: ",
                         "Model: Raspberry Pi 3", "Docker Compose: 5.5.0",
                         "Update signature: CoffeePy's key 0123456789abcdef",
                         "OS bundle: os-trixie-20261030-1200 (applied 2026-",
                         "web: coffeepy-web:latest @0123456789ab", "migrate: coffeepy-web:latest @0123456789ab (exited)"):
            self.assertIn(expected, text)
        self.assertNotIn("Committed:", text)  # nothing to say - left out
        self.assertContains(response, "Copy all")
        self.assertContains(response, "<code>0be2def64ad5</code>", html=True)
        self.assertNotContains(response, "appear once the update watcher")

    def test_signature_states(self):
        for signature, shown in (({"check": False}, "off - every update is installed"),
                                 ({"check": True, "key": "missing"}, "no key on the device"),
                                 ({"check": True, "key": "own", "fingerprint": "abc"}, "own key abc"),
                                 ({}, "CoffeePy's key")):
            with self.subTest(shown=shown):
                text = self.page({**self.SYSTEM, "signature": signature}).context["version_text"]
                self.assertIn(f"Update signature: {shown}", text)

    def test_before_the_watcher_wrote_anything(self):
        response = self.page()
        self.assertContains(response, "appear once the update watcher")
        self.assertNotIn("[Device]", response.context["version_text"])
        self.assertIn("Version: dev", response.context["version_text"])
        (self.updates / "system.json").write_text("[]")  # not what the watcher writes
        self.assertContains(self.client.get(reverse("dashboard:update")), "appear once the update watcher")

    def test_a_device_with_no_containers_yet(self):
        response = self.page({"updated": 0, "containers": []})
        self.assertNotIn("[Containers]", response.context["version_text"])

    def test_helpers_when_something_is_missing(self):
        self.assertEqual(versions._os_bundle({}), "")
        self.assertEqual(versions._os_bundle({"version": "os-trixie-1"}), "os-trixie-1")
        self.assertEqual(versions._package("no-such-package-here"), "")
        with mock.patch.object(versions.subprocess, "run", side_effect=OSError):
            self.assertEqual(versions._pg_dump(), "")
        with mock.patch.object(versions.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout=b"pg_dump (PostgreSQL) 17.6\n")):
            self.assertEqual(versions._pg_dump(), "pg_dump (PostgreSQL) 17.6")
        with mock.patch.object(versions.subprocess, "run", return_value=mock.Mock(returncode=1, stdout=b"")):
            self.assertEqual(versions._pg_dump(), "")

    def test_database(self):
        shown = versions._database()
        if versions.connection.vendor == "postgresql":
            self.assertTrue(shown.startswith("PostgreSQL "))
        else:
            self.assertTrue(shown.startswith("sqlite "))
        with mock.patch.object(versions, "connection", mock.Mock(vendor="mysql")):
            self.assertEqual(versions._database(), "")
