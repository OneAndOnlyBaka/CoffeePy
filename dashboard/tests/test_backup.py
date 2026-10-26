import json
import os
import tarfile
import time
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from coffee import backups
from coffee.models import AuditLogEntry, BackupSettings, CoffeeSort, Member
from coffee.tests.device import FakeDevice, needs_postgres
from dashboard import restore_jobs

DISK = {"path": "/dev/sda", "vendor": "SanDisk", "model": "Cruzer Edge", "serial": "2004351400051AF3854F",
        "size": 16000000000, "volumes": [{"path": "/dev/sda", "fstype": "vfat", "label": "OLD", "uuid": "F3EF"}]}


def device_backup(path: Path, migrations=("coffee.0001_initial",), config=None) -> Path:
    """A minimal device backup (no real dump in it - for what doesn't load it)."""
    with tarfile.open(path, "w") as tar:
        for name, data in {"manifest.json": json.dumps({"migrations": list(migrations)}), "database.dump": "PGDMP",
                           **(config or {})}.items():
            source = path.with_name("src-" + name.replace("/", "_"))
            source.write_text(data)
            tar.add(source, name)
    return path


class AdminTestCase(FakeDevice, TestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")

    def requests(self):
        directory = self.storage / "requests"
        return [json.loads(p.read_text()) for p in directory.glob("*.json")] if directory.exists() else []


class BackupPageTestCase(AdminTestCase):
    def test_managers_are_kept_out(self):
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        self.assertRedirects(client.get(reverse("dashboard:backup")), reverse("dashboard:index"))

    def test_page_with_this_devices_stick_and_another(self):
        target = self.set_up_stick()
        target.mkdir()
        (target / "coffeepy_2026-10-26_010000.backup").write_text("x")
        backups.write_checksum(target / "coffeepy_2026-10-26_010000.backup")
        (target / "old.dump").write_text("x")
        self.set_up_stick(device="device-b", where="foreign")
        self.helper_alive(own={"model": "SanDisk Cruzer", "space": {"free": 1, "total": 2}},
                          foreign={"meta": {"device_id": "device-b", "device_name": "kitchen", "set_up_at": "2026-01-01"}},
                          available=[DISK], configured={"stick_id": "stick-1"})
        response = self.client.get(reverse("dashboard:backup"))
        for text in ("coffeepy_2026-10-26_010000.backup", "checked", "data only", "A backup stick of another CoffeePy",
                     "Set up as backup stick", "not encrypted", "Back up every"):
            self.assertContains(response, text)
        self.set_up_stick(device="device-a", stick_id="stick-0", where="foreign")
        self.helper_alive(foreign={"meta": {"device_id": "device-a", "device_name": "this", "set_up_at": "2026-01-01"}})
        self.assertContains(self.client.get(reverse("dashboard:backup")), "An earlier backup stick of this device")

    def test_back_up_now(self):
        with mock.patch("dashboard.views_backup.call_command") as command:
            self.client.post(reverse("dashboard:backup_now"))
            command.assert_called_once_with("backup_db", once=True)
            command.side_effect = Exception("no stick")
            response = self.client.post(reverse("dashboard:backup_now"), follow=True)
        self.assertContains(response, "no stick")
        self.assertEqual(list(AuditLogEntry.objects.filter(action="backup.trigger").order_by("id").values_list("outcome", flat=True)),
                         ["success", "failure"])

    def test_settings(self):
        data = {"interval_minutes": 30, "keep_recent": 24, "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 6}
        self.client.post(reverse("dashboard:backup_settings"), data)
        self.assertEqual(BackupSettings.current().interval_minutes, 30)
        response = self.client.post(reverse("dashboard:backup_settings"), {**data, "interval_minutes": 1}, follow=True)
        self.assertContains(response, "Allowed")
        self.assertEqual(BackupSettings.current().interval_minutes, 30)

    def test_stick_status_for_polling(self):
        self.helper_alive(request={"state": "done"})
        self.assertEqual(self.client.get(reverse("dashboard:storage_status")).json()["request"], {"state": "done"})

    def test_setting_up_a_stick_only_when_confirmed(self):
        self.helper_alive(available=[DISK])
        url = reverse("dashboard:usb_setup")
        self.assertContains(self.client.get(f"{url}?disk=/dev/sda"), DISK["serial"])
        self.client.post(url, {"disk": "/dev/sda"})
        self.assertEqual(self.requests(), [])
        self.client.post(url, {"disk": "/dev/sda", "confirm": "1"})
        self.assertEqual([(r["action"], r["serial"]) for r in self.requests()], [("format", DISK["serial"])])
        self.assertRedirects(self.client.get(f"{url}?disk=/dev/sdz"), reverse("dashboard:backup"))

    def test_adopt_and_eject(self):
        for action in ("adopt", "eject"):
            self.client.post(reverse("dashboard:usb_action"), {"action": action})
        self.assertEqual(sorted(r["action"] for r in self.requests()), ["adopt", "eject"])
        self.assertEqual(self.client.post(reverse("dashboard:usb_action"), {"action": "format"}).status_code, 404)

    def test_download(self):
        target = self.set_up_stick()
        target.mkdir()
        (target / "coffeepy_x.backup").write_text("BACKUP")
        response = self.client.get(reverse("dashboard:backup_download", args=["own", "coffeepy_x.backup"]))
        self.assertEqual(b"".join(response.streaming_content), b"BACKUP")
        self.assertEqual(self.client.get(reverse("dashboard:backup_download", args=["own", "nope.backup"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:backup_download", args=["foreign", "x.backup"])).status_code, 404)

    def test_export(self):
        def fake_backup(directory):
            path = Path(directory) / "coffeepy_now.backup"
            path.write_text("ALL")
            return path
        with mock.patch.object(backups, "create_backup", side_effect=fake_backup):
            response = self.client.get(reverse("dashboard:backup_export"))
        self.assertEqual(response.content, b"ALL")
        self.assertIn("coffeepy_now.backup", response["Content-Disposition"])
        with mock.patch.object(backups, "create_backup", side_effect=backups.BackupError("pg_dump: down")):
            response = self.client.get(reverse("dashboard:backup_export"), follow=True)
        self.assertContains(response, "pg_dump: down")


class RestorePageTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.target = self.set_up_stick()
        self.target.mkdir()
        device_backup(self.target / "coffeepy_x.backup")
        (self.target / "old.dump").write_text("PGDMP")

    def test_confirmation_and_parts(self):
        url = reverse("dashboard:backup_restore", args=["own", "coffeepy_x.backup"])
        page = self.client.get(url)
        self.assertContains(page, "Device identity")
        with mock.patch.object(restore_jobs, "start_restore") as start:
            self.client.post(url, {"confirm": "nope", "parts": ["database"]})
            start.assert_not_called()
            self.client.post(url, {"confirm": "DELETE", "parts": ["database", "certificates"], "passphrase": "other"})
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["parts"], ["database", "certificates"])
        self.assertEqual(start.call_args.kwargs["secret"], "other")

    def test_older_dumps_offer_only_the_data(self):
        page = self.client.get(reverse("dashboard:backup_restore", args=["own", "old.dump"]))
        self.assertNotContains(page, "Device identity")

    def test_from_another_devices_stick_everything_is_ticked(self):
        foreign = self.set_up_stick(device="device-b", where="foreign")
        foreign.mkdir()
        device_backup(foreign / "coffeepy_y.backup")
        page = self.client.get(reverse("dashboard:backup_restore", args=["foreign", "coffeepy_y.backup"]))
        self.assertEqual(page.context["form"].initial["parts"], list(restore_jobs.PARTS))

    def test_one_at_a_time(self):
        url = reverse("dashboard:backup_restore", args=["own", "coffeepy_x.backup"])
        restore_jobs.write_status(state="running")
        with mock.patch.object(restore_jobs, "start_restore") as start:
            response = self.client.post(url, {"confirm": "DELETE", "parts": ["database"]})
            start.assert_not_called()
        self.assertContains(response, "already running")
        restore_jobs.write_status(state="idle")
        (self.root / "maintenance").mkdir()
        (self.root / "maintenance" / "status.json").write_text(json.dumps({"kind": "update", "updated": time.time()}))
        response = self.client.post(url, {"confirm": "DELETE", "parts": ["database"]})
        self.assertContains(response, "being updated")

    def test_missing_file(self):
        url = reverse("dashboard:backup_restore", args=["own", "gone.backup"])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_status_for_polling(self):
        restore_jobs.write_status(state="running", phase="Restoring the backup", percent=40)
        self.assertEqual(self.client.get(reverse("dashboard:restore_status")).json()["percent"], 40)


class RestoreUploadTestCase(AdminTestCase):
    url = "dashboard:backup_restore_upload"

    def post(self, content, name="x.backup", **extra):
        data = {"confirm": "DELETE", "parts": ["database"], "backup": SimpleUploadedFile(name, content), **extra}
        return self.client.post(reverse(self.url), data, follow=True)

    def test_page(self):
        self.assertContains(self.client.get(reverse(self.url)), "Backup file")

    def test_a_good_backup_is_restored(self):
        content = device_backup(self.root / "src.backup").read_bytes()
        with mock.patch.object(restore_jobs, "start_restore") as start:
            self.post(content)
        start.assert_called_once()
        self.assertTrue(start.call_args.kwargs["delete_after"])

    def test_rejected_before_anything_changes(self):
        newer = device_backup(self.root / "new.backup", migrations=["coffee.9999_future"]).read_bytes()
        with mock.patch.object(restore_jobs, "start_restore") as start:
            self.assertContains(self.post(b"SQLite format 3\x00"), "Import legacy DB")
            self.assertContains(self.post(newer), "newer version")
            self.assertContains(self.client.post(reverse(self.url), {"confirm": "DELETE", "parts": ["database"]},
                                                 follow=True), "Please choose a backup file")
            start.assert_not_called()
        self.assertEqual(list((self.storage / "uploads").iterdir()), [])  # nothing left lying around

    def test_the_confirmation_is_needed_here_too(self):
        with mock.patch.object(restore_jobs, "start_restore") as start:
            response = self.post(device_backup(self.root / "src.backup").read_bytes(), confirm="no")
        start.assert_not_called()
        self.assertContains(response, "Type DELETE to confirm")

    def test_one_at_a_time(self):
        import_status = {"state": "running", "updated": time.time()}
        with mock.patch("dashboard.import_jobs.read_status", return_value=import_status):
            response = self.post(device_backup(self.root / "src.backup").read_bytes())
        self.assertContains(response, "already running")


class RestoreJobTestCase(FakeDevice, TransactionTestCase):
    # The job runs in a thread with its own database connection - what it writes is not
    # inside a test transaction (see CLAUDE.md, "Background threads").

    def test_a_failed_restore_reports_and_keeps_a_copy(self):
        backup = device_backup(self.root / "broken.backup")
        with mock.patch.object(backups, "create_backup", return_value=self.root / "before-restore_x.backup") as safety, \
                mock.patch.object(backups.Backup, "check_database"), \
                mock.patch.object(backups, "restore_database", side_effect=backups.BackupError("psql: broken")):
            restore_jobs.start_restore(backup, "broken.backup", "boss").join()
        safety.assert_called_once()
        status = restore_jobs.localized(restore_jobs.read_status())
        self.assertEqual(status["state"], "error")
        self.assertIn("psql: broken", status["message"])
        self.assertFalse((self.root / "maintenance" / "status.json").exists())  # kiosk back to normal
        self.assertEqual(AuditLogEntry.objects.get(action="backup.restore").outcome, "failure")

    def test_everything_of_a_device_backup(self):
        backup = device_backup(self.root / "full.backup", config={
            "config/tls/ca.crt": "CA", "config/device-id": "device-old", "config/env": "PUBLIC_HOST=10.0.0.5\n"})
        upload = self.root / "upload.backup"
        upload.write_bytes(backup.read_bytes())
        with mock.patch.object(backups, "create_backup", return_value=self.root / "before-restore_x.backup"), \
                mock.patch.object(backups.Backup, "check_database"), \
                mock.patch.object(backups, "restore_database"), \
                mock.patch("dashboard.restore_jobs.call_command") as command:
            restore_jobs.start_restore(upload, "full.backup", "boss", parts=restore_jobs.PARTS, delete_after=True).join()
        self.assertEqual(restore_jobs.read_status()["state"], "done")
        self.assertEqual([c.args[0] for c in command.call_args_list], ["ensure_tls", "migrate"])
        self.assertEqual((self.tls / "ca.crt").read_text(), "CA")
        self.assertEqual(backups.device_id(), "device-old")
        request = json.loads(next((self.storage / "requests").glob("*.json")).read_text())
        self.assertEqual((request["action"], request["values"]), ("apply_settings", {"PUBLIC_HOST": "10.0.0.5"}))
        self.assertFalse(upload.exists())
        entry = AuditLogEntry.objects.get(action="backup.restore")
        self.assertIn("database, certificates, identity, settings", entry.details)
        self.assertIn("Log in again", restore_jobs.localized(restore_jobs.read_status())["message"])

    def test_only_the_certificates(self):
        backup = device_backup(self.root / "certs.backup", config={"config/tls/ca.crt": "CA"})
        with mock.patch.object(backups, "create_backup", return_value=self.root / "before-restore_x.backup"), \
                mock.patch.object(backups.Backup, "check_database"), \
                mock.patch.object(backups, "restore_database") as database, \
                mock.patch("dashboard.restore_jobs.call_command"):
            restore_jobs.start_restore(backup, "certs.backup", "boss", parts=["certificates"]).join()
        database.assert_not_called()
        self.assertEqual((self.tls / "ca.crt").read_text(), "CA")

    def test_a_copy_before_restoring_goes_to_the_sd_card_without_a_stick(self):
        directory = restore_jobs._safety_dir()
        self.assertEqual(directory, self.storage / "before-restore")
        self.set_up_stick()
        self.assertEqual(restore_jobs._safety_dir(), self.usb / "backup" / "backups")

    def test_status_files(self):
        self.assertEqual(restore_jobs.read_status()["state"], "idle")
        self.assertFalse(restore_jobs.is_running())
        restore_jobs.write_status(state="running", phase="Restoring the backup")
        self.assertTrue(restore_jobs.is_running())
        self.assertEqual(restore_jobs.localized(restore_jobs.read_status())["phase"], "Restoring the backup")
        self.assertIsNone(restore_jobs.maintenance_active())
        (self.root / "maintenance").mkdir()
        (self.root / "maintenance" / "status.json").write_text(json.dumps({"kind": "update", "updated": time.time()}))
        self.assertEqual(restore_jobs.maintenance_active(), "update")
        (self.root / "maintenance" / "status.json").write_text(json.dumps({"kind": "update", "updated": 0}))
        self.assertIsNone(restore_jobs.maintenance_active())

    def test_maintenance_screen_survives_a_read_only_directory(self):
        with mock.patch.object(Path, "write_text", side_effect=OSError("read-only")):
            restore_jobs._maintenance("restore", 40)  # no exception: the kiosk is a courtesy here


@needs_postgres
class RealRestoreTestCase(FakeDevice, TransactionTestCase):
    def test_restore_end_to_end(self):
        CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        Member.objects.create(uid=1, nickname="Anna")
        target = self.set_up_stick()
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": "Kaffee-ist-fertig-42"}):
            path = backups.create_backup(target)
            Member.objects.create(uid=2, nickname="Ben")
            connections.close_all()
            restore_jobs.start_restore(path, path.name, "boss").join()
        connections.close_all()  # the restore cut this thread's connection too
        self.assertEqual(restore_jobs.read_status()["state"], "done", restore_jobs.read_status())
        self.assertEqual(list(Member.objects.values_list("nickname", flat=True)), ["Anna"])
        self.assertEqual(len(list(target.glob("before-restore_*.backup.enc"))), 1)


class KioskAlarmTestCase(FakeDevice, TestCase):
    def panel(self):
        client = Client()
        client.get(reverse("kiosk:index"))
        return client.get(reverse("kiosk:panel"))

    def test_shown_only_while_backups_are_disturbed(self):
        CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        self.assertNotContains(self.panel(), "backup-alarm")  # never set up: nothing is broken
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "stick-1"}))
        self.assertContains(self.panel(), "backup-alarm")  # set up, stick missing
        self.set_up_stick()
        self.assertNotContains(self.panel(), "backup-alarm")

    def test_overview_names_the_missing_stick(self):
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "stick-1", "model": "SanDisk", "serial": "ABC"}))
        response = client.get(reverse("dashboard:index"))
        self.assertContains(response, "plug the stick (SanDisk, serial ABC) back in")
        for state, text in (("stale", "No backup for a long time"), ("failing", "The last backup failed"),
                            ("helper", "storage service isn"), ("ok", "backing up")):
            with mock.patch.object(backups, "health", return_value={"state": state, "disturbed": state != "ok",
                                                                    "error": "x", "configured": {}, "last_ok": None}):
                self.assertContains(client.get(reverse("dashboard:index")), text, msg_prefix=state)

    def test_backup_page_health_card(self):
        User.objects.create_superuser(username="boss", password="pw")
        client = Client()
        client.login(username="boss", password="pw")
        for state, text in (("stale", "No backup for a long time"), ("failing", "The last backup failed"),
                            ("helper", "storage service isn"), ("none", "No backups yet"), ("ok", "Backups are running")):
            with mock.patch.object(backups, "health", return_value={"state": state, "disturbed": False, "error": "x",
                                                                    "configured": {}, "last_ok": None, "encrypted": False}):
                self.assertContains(client.get(reverse("dashboard:backup")), text, msg_prefix=state)
