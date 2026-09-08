import json
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from coffee.models import CoffeeSort, Member
from dashboard import import_jobs

LEGACY_SCHEMA = """
CREATE TABLE coffee_sorts(coffee_name TEXT, coffee_price REAL, coffee_machine_strokes INT);
CREATE TABLE user(user_uid INTEGER PRIMARY KEY, user_alt_uid INTEGER UNIQUE DEFAULT NULL,
                  user_nick_name TEXT, user_favourite_coffee INT);
CREATE TABLE coffee_order(user_uid INTEGER, order_value REAL, order_coffee_machine_strokes INT,
                          order_timestamp INT, order_datetime TEXT, order_info TEXT);
CREATE TABLE payments(user_uid INTEGER, payment_value REAL, payment_timestamp INT,
                      payment_datetime TEXT, payment_info TEXT);
INSERT INTO coffee_sorts VALUES ('espresso', 0.3, 1);
INSERT INTO user VALUES (42, NULL, 'Alice', 1);
"""


# The import runs in a background thread with its own database connection, so it needs a
# committed database rather than TestCase's wrapping transaction.
class ImportLegacyDbViewTestCase(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(username="root", password="pw")
        self._status_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._status_dir.cleanup)
        self._settings = override_settings(
            IMPORT_STATUS_FILE=Path(self._status_dir.name) / "import.json"
        )
        self._settings.enable()
        self.addCleanup(self._settings.disable)

    def upload(self, name: str, content: bytes, **extra):
        upload = SimpleUploadedFile(name, content, content_type="application/octet-stream")
        return self.client.post(reverse("dashboard:import_legacy_db"), {"dump": upload, **extra})

    def wait_for_import(self, timeout: float = 30.0) -> dict:
        """Polls the status endpoint the way the page does, until the job is finished."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = json.loads(self.client.get(reverse("dashboard:import_status")).content)
            if status["state"] in ("done", "error"):
                return status
            time.sleep(0.05)
        self.fail("import did not finish in time")

    def test_requires_login(self):
        response = self.client.get(reverse("dashboard:import_legacy_db"))
        self.assertEqual(response.status_code, 302)

    def test_status_requires_login(self):
        response = self.client.get(reverse("dashboard:import_status"))
        self.assertEqual(response.status_code, 302)

    def test_non_superuser_is_rejected(self):
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        response = self.client.get(reverse("dashboard:import_legacy_db"))
        self.assertEqual(response.status_code, 302)

    def test_uploading_dump_imports_data(self):
        self.client.login(username="root", password="pw")

        response = self.upload("coffee_backup.db", LEGACY_SCHEMA.encode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["state"], "running")
        status = self.wait_for_import()
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["percent"], 100)
        self.assertIn("Imported 1 coffee sorts, 1 members", status["message"])
        self.assertEqual(CoffeeSort.objects.count(), 1)
        self.assertEqual(Member.objects.get(uid=42).nickname, "Alice")

    def test_import_merges_into_existing_data_by_default(self):
        self.client.login(username="root", password="pw")
        Member.objects.create(uid=7, nickname="Bob")

        self.upload("coffee_backup.db", LEGACY_SCHEMA.encode())

        self.assertEqual(self.wait_for_import()["state"], "done")
        self.assertTrue(Member.objects.filter(uid=7).exists())

    def test_purge_checkbox_clears_existing_data_first(self):
        self.client.login(username="root", password="pw")
        Member.objects.create(uid=7, nickname="Bob")

        self.upload("coffee_backup.db", LEGACY_SCHEMA.encode(), purge="1")

        status = self.wait_for_import()
        self.assertEqual(status["state"], "done")
        self.assertIn("Cleared", status["message"])
        self.assertFalse(Member.objects.filter(uid=7).exists())
        self.assertTrue(Member.objects.filter(uid=42).exists())

    def test_failed_purging_import_leaves_existing_data_alone(self):
        """Clearing happens in the import's transaction - a broken dump must not wipe."""
        self.client.login(username="root", password="pw")
        Member.objects.create(uid=7, nickname="Bob")

        self.upload("not_a_db.db", b"not a database", purge="1")

        self.assertEqual(self.wait_for_import()["state"], "error")
        self.assertTrue(Member.objects.filter(uid=7).exists())

    def test_page_resumes_a_running_import(self):
        self.client.login(username="root", password="pw")
        import_jobs.write_status(state="running", phase="Orders", done=7, total=10, percent=70)

        response = self.client.get(reverse("dashboard:import_legacy_db"))

        self.assertContains(response, "import-progress")
        self.assertContains(response, 'running("Orders - 70%", 70)')

    def test_status_is_idle_before_any_import(self):
        self.client.login(username="root", password="pw")
        status = json.loads(self.client.get(reverse("dashboard:import_status")).content)
        self.assertEqual(status["state"], "idle")

    def test_no_file_shows_error(self):
        self.client.login(username="root", password="pw")
        response = self.client.post(reverse("dashboard:import_legacy_db"), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Please choose a file", json.loads(response.content)["error"])

    def test_garbage_file_reports_failure(self):
        self.client.login(username="root", password="pw")

        self.upload("not_a_db.db", b"not a database")

        status = self.wait_for_import()
        self.assertEqual(status["state"], "error")
        self.assertIn("Import failed", status["message"])

    def test_second_import_is_rejected_while_one_runs(self):
        self.client.login(username="root", password="pw")
        import_jobs.write_status(state="running", phase="Orders", percent=10)

        response = self.upload("coffee_backup.db", LEGACY_SCHEMA.encode())

        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", json.loads(response.content)["error"])

    def test_stale_running_status_does_not_block_a_new_import(self):
        """A worker killed mid-import must not lock the page out for good."""
        self.client.login(username="root", password="pw")
        import_jobs.write_status(state="running", phase="Orders", percent=10)
        stale = {**import_jobs.read_status(), "updated": time.time() - import_jobs.STALE_AFTER_SECONDS - 1}
        settings.IMPORT_STATUS_FILE.write_text(json.dumps(stale))

        self.upload("coffee_backup.db", LEGACY_SCHEMA.encode())

        self.assertEqual(self.wait_for_import()["state"], "done")
