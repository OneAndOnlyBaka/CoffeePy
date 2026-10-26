import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee import backups


class StickTestCase(TestCase):
    """A fake host: STORAGE_DIR and USB_DIR in a temp dir, filled the way the storage helper
    (install/storage_helper.py) fills them, and pg_dump replaced by a file write."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.storage, self.usb = self.root / "storage", self.root / "usb"
        (self.usb / "backup").mkdir(parents=True)
        (self.usb / "foreign").mkdir(parents=True)
        self.storage.mkdir()
        (self.storage / "device-id").write_text("device-a\n")
        overrides = override_settings(STORAGE_DIR=self.storage, USB_DIR=self.usb,
                                      RESTORE_STATUS_FILE=self.root / "restore.json",
                                      MAINTENANCE_DIR=self.root / "maintenance")
        overrides.enable()
        self.addCleanup(overrides.disable)
        patcher = mock.patch.object(backups, "dump", side_effect=lambda target: (target.write_text("PGDMP"), target)[1])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.helper_alive()

    def helper_alive(self, **fields):
        (self.storage / "status.json").write_text(json.dumps({"updated": time.time(), "available": [], **fields}))

    def set_up_stick(self, stick_id="stick-1", device="device-a", where="backup"):
        (self.usb / where / ".coffeepy-backup.json").write_text(json.dumps({"device_id": device, "stick_id": stick_id,
                                                                             "device_name": "coffeepy"}))
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "stick-1", "model": "SanDisk Cruzer",
                                                             "serial": "ABC"}))


class BackupCommandTestCase(StickTestCase):
    def test_nothing_is_written_without_the_stick(self):
        # The mount point is a plain directory on the SD card while no stick is in.
        call_command("backup_db")
        self.assertEqual(list((self.usb / "backup").iterdir()), [])
        with self.assertRaises(CommandError):
            call_command("backup_db", once=True)

    def test_backs_up_to_this_devices_stick_and_keeps_the_newest(self):
        self.set_up_stick()
        with mock.patch.dict("os.environ", {"BACKUP_DEPTH": "2"}):
            for stamp in ("2026-01-01_000000", "2026-01-02_000000", "2026-01-03_000000"):
                with mock.patch.object(backups.timezone, "localtime") as now:
                    now.return_value.strftime.return_value = stamp
                    call_command("backup_db")
        names = sorted(p.name for p in (self.usb / "backup" / "backups").iterdir())
        self.assertEqual(names, ["coffeepy_2026-01-02_000000.dump", "coffeepy_2026-01-03_000000.dump"])
        self.assertEqual(backups.health()["state"], "ok")

    def test_a_forgotten_stick_of_this_device_is_not_written(self):
        # An older stick this device used before a new one was set up: same device id,
        # different stick id - it is only read now.
        self.set_up_stick(stick_id="stick-0")
        self.assertIsNone(backups.own_backup_dir())

    def test_another_devices_stick_is_not_written(self):
        self.set_up_stick(device="device-b")
        self.assertIsNone(backups.own_backup_dir())


class HealthTestCase(StickTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")

    def test_never_set_up(self):
        self.assertEqual(backups.health()["state"], "none")
        self.assertContains(self.client.get(reverse("dashboard:index")), "None set up - backups are not running.")

    def test_stick_pulled_out_shows_on_the_overview(self):
        self.set_up_stick()
        (self.usb / "backup" / ".coffeepy-backup.json").unlink()  # unplugged: the mount is gone
        self.assertEqual(backups.health()["state"], "missing")
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "Backups are not running - plug the stick (SanDisk Cruzer, serial ABC) back in.")

    def test_same_stick_back_in_carries_on(self):
        self.set_up_stick()
        self.assertEqual(backups.health()["state"], "ok")
        self.assertContains(self.client.get(reverse("dashboard:index")), "backing up")

    def test_helper_down(self):
        (self.storage / "status.json").write_text(json.dumps({"updated": time.time() - 3600}))
        self.assertEqual(backups.health()["state"], "helper")

    def test_managers_see_the_warning_too(self):
        self.set_up_stick()
        (self.usb / "backup" / ".coffeepy-backup.json").unlink()
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        self.assertContains(client.get(reverse("dashboard:index")), "plug the stick")


class BackupPageTestCase(StickTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")
        self.disk = {"path": "/dev/sda", "vendor": "SanDisk", "model": "Cruzer Edge", "serial": "2004351400051AF3854F",
                     "size": 16000000000, "volumes": [{"path": "/dev/sda", "fstype": "vfat", "label": "", "uuid": "F3EF"}]}

    def requests(self):
        return [json.loads(p.read_text()) for p in (self.storage / "requests").glob("*.json")]

    def test_a_plugged_in_stick_is_offered_but_only_formatted_when_confirmed(self):
        self.helper_alive(available=[self.disk])
        self.assertContains(self.client.get(reverse("dashboard:backup")), "Set up as backup stick")
        url = f"{reverse('dashboard:usb_setup')}?disk=/dev/sda"
        self.assertContains(self.client.get(url), "2004351400051AF3854F")
        self.client.post(reverse("dashboard:usb_setup"), {"disk": "/dev/sda"})
        self.assertFalse((self.storage / "requests").exists() and self.requests())
        self.client.post(reverse("dashboard:usb_setup"), {"disk": "/dev/sda", "confirm": "1"})
        self.assertEqual([(r["action"], r["path"], r["serial"]) for r in self.requests()],
                         [("format", "/dev/sda", "2004351400051AF3854F")])

    def test_a_stick_that_is_gone_is_not_formatted(self):
        self.client.post(reverse("dashboard:usb_setup"), {"disk": "/dev/sda", "confirm": "1"})
        self.assertFalse((self.storage / "requests").exists())

    def test_backups_on_the_stick_are_listed_and_downloadable(self):
        self.set_up_stick()
        self.helper_alive(own={"model": "SanDisk Cruzer", "space": {"free": 1, "total": 2}})
        (self.usb / "backup" / "backups").mkdir()
        (self.usb / "backup" / "backups" / "coffeepy_2026-10-26_010000.dump").write_text("PGDMP")
        response = self.client.get(reverse("dashboard:backup"))
        self.assertContains(response, "coffeepy_2026-10-26_010000.dump")
        download = self.client.get(reverse("dashboard:backup_download", args=["own", "coffeepy_2026-10-26_010000.dump"]))
        self.assertEqual(b"".join(download.streaming_content), b"PGDMP")
        self.assertEqual(self.client.get(reverse("dashboard:backup_download", args=["own", "..%2Fstick.json"])).status_code, 404)

    def test_another_devices_stick_can_be_taken_over(self):
        self.set_up_stick(device="device-b", where="foreign")
        self.helper_alive(foreign={"meta": {"device_id": "device-b", "device_name": "kitchen", "set_up_at": "2026-01-01"}})
        self.assertContains(self.client.get(reverse("dashboard:backup")), "A backup stick of another CoffeePy")
        self.client.post(reverse("dashboard:usb_action"), {"action": "adopt"})
        self.assertEqual([r["action"] for r in self.requests()], ["adopt"])

    def test_restore_needs_the_confirmation_word(self):
        self.set_up_stick()
        (self.usb / "backup" / "backups").mkdir()
        (self.usb / "backup" / "backups" / "coffeepy_x.dump").write_text("PGDMP")
        url = reverse("dashboard:backup_restore", args=["own", "coffeepy_x.dump"])
        with mock.patch("dashboard.restore_jobs.start_restore") as start:
            self.client.post(url, {"confirm": "nope"})
            start.assert_not_called()
            self.client.post(url, {"confirm": "DELETE"})
            start.assert_called_once()

    def test_an_old_sqlite_database_is_sent_to_the_import(self):
        with tempfile.NamedTemporaryFile(suffix=".sql") as f:
            f.write(b"SQLite format 3\x00")
            f.seek(0)
            with mock.patch("dashboard.restore_jobs.start_restore") as start:
                response = self.client.post(reverse("dashboard:backup_restore_upload"),
                                            {"backup": f, "confirm": "DELETE"}, follow=True)
                start.assert_not_called()
        self.assertContains(response, "Import legacy DB")


class RestoreJobTestCase(StickTestCase):
    def test_a_failed_restore_reports_and_keeps_a_copy(self):
        # The restore is one transaction, so a failure leaves the data as it was; the copy
        # taken first is for a backup that loads but turns out to be the wrong one.
        from dashboard import restore_jobs

        backup = self.root / "broken.dump"
        backup.write_text("PGDMP")
        with mock.patch.object(backups, "check_restorable", return_value="custom"), \
                mock.patch.object(backups, "restore", side_effect=backups.BackupError("psql: broken")) as restore:
            restore_jobs.start_restore(backup, "broken.dump", "boss").join()
        restore.assert_called_once_with(backup)
        self.assertEqual(restore_jobs.read_status()["state"], "error")
        self.assertTrue(list((self.storage / "before-restore").glob("before-restore_*.dump")))
        self.assertFalse((self.root / "maintenance" / "status.json").exists())  # kiosk back to normal


class StorageHelperTestCase(TestCase):
    """The host helper's choice of disks - pure functions, no root needed."""

    def setUp(self):
        import importlib.util

        from django.conf import settings
        spec = importlib.util.spec_from_file_location("storage_helper", settings.BASE_DIR / "install" / "storage_helper.py")
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)

    def test_the_system_disk_is_never_offered_even_on_usb(self):
        devices = [
            {"name": "sda", "path": "/dev/sda", "type": "disk", "tran": "usb", "size": 16, "serial": "S1",
             "fstype": "vfat", "label": None, "uuid": "U1"},
            {"name": "sdb", "path": "/dev/sdb", "type": "disk", "tran": "usb", "size": 500, "serial": "S2", "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "vfat"},
                {"name": "sdb2", "path": "/dev/sdb2", "type": "part", "fstype": "ext4"},
            ]},
            {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "tran": "mmc", "size": 64},
        ]
        disks = self.helper.usb_disks(devices, {"/": "/dev/sdb2", "/boot/firmware": "/dev/sdb1"})
        self.assertEqual([d["path"] for d in disks], ["/dev/sda"])
        self.assertEqual([v["path"] for v in disks[0]["volumes"]], ["/dev/sda"])  # no partition table
