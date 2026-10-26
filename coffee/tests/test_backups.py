import json
import os
import tarfile
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import CommandError, call_command
from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from coffee import backups
from coffee.models import AuditLogEntry, BackupSettings, CoffeeSort, Member, Order

from .device import FakeDevice, needs_openssl, needs_postgres


def touch(path: Path, text="x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class StickTestCase(FakeDevice, TestCase):
    def test_status_of_a_helper_that_never_ran(self):
        (self.storage / "status.json").unlink()
        self.assertEqual(backups.storage_status(), {"helper_running": False})

    def test_status_of_a_running_helper(self):
        self.assertTrue(backups.storage_status()["helper_running"])

    def test_device_id_and_stick(self):
        self.assertEqual(backups.device_id(), "device-a")
        (self.storage / "device-id").unlink()
        self.assertEqual(backups.device_id(), "")
        self.assertIsNone(backups.configured_stick())
        (self.storage / "stick.json").write_text("{broken")
        self.assertIsNone(backups.configured_stick())

    def test_only_this_devices_current_stick_is_written(self):
        self.assertIsNone(backups.own_backup_dir())  # no stick mounted
        (self.usb / "backup" / backups.META).write_text(json.dumps({"device_id": "device-a", "stick_id": "stick-1"}))
        self.assertIsNone(backups.own_backup_dir())  # never set up
        self.set_up_stick(device="device-b")
        self.assertIsNone(backups.own_backup_dir())  # another device's
        self.set_up_stick(stick_id="stick-0")
        self.assertIsNone(backups.own_backup_dir())  # this device's, but replaced by a newer stick
        self.set_up_stick()
        self.assertEqual(backups.own_backup_dir(), self.usb / "backup" / "backups")
        self.assertTrue(backups.own_backup_dir().is_dir())

    def test_foreign_stick(self):
        self.assertIsNone(backups.foreign_backup_dir())
        self.set_up_stick(device="device-b", where="foreign")
        self.assertEqual(backups.foreign_backup_dir(), self.usb / "foreign" / "backups")

    def test_requests_for_the_helper(self):
        request_id = backups.storage_request("eject", requested_by="boss")
        request = json.loads((self.storage / "requests" / f"{request_id}.json").read_text())
        self.assertEqual(request, {"id": request_id, "action": "eject", "requested_by": "boss"})


class FilesTestCase(FakeDevice, TestCase):
    def setUp(self):
        super().setUp()
        self.dir = self.root / "backups"
        self.dir.mkdir()

    def test_backup_names(self):
        for name in ("coffeepy_2026-01-01_000000.backup", "x.backup.enc", "old.dump", "old.sql"):
            self.assertTrue(backups.is_backup_name(name), name)
        for name in (".part.backup", "x.sha256", "notes.txt"):
            self.assertFalse(backups.is_backup_name(name), name)

    def test_checksums(self):
        path = touch(self.dir / "a.backup", "content")
        self.assertEqual(backups.verify(path), "unknown")
        backups.write_checksum(path)
        self.assertEqual(backups.verify(path), "ok")
        path.write_text("changed")
        self.assertEqual(backups.verify(path), "damaged")
        backups.checksum_path(path).write_text("")
        self.assertEqual(backups.verify(path), "unknown")

    def test_list(self):
        self.assertEqual(backups.list_backups(None), [])
        self.assertEqual(backups.list_backups(self.root / "missing"), [])
        touch(self.dir / "coffeepy_2026-01-01_000000.backup.enc")
        touch(self.dir / "old.dump")
        touch(self.dir / "notes.txt")
        os.utime(self.dir / "old.dump", (1, 1))
        listed = backups.list_backups(self.dir)
        self.assertEqual([(f["name"], f["encrypted"], f["device"]) for f in listed],
                         [("coffeepy_2026-01-01_000000.backup.enc", True, True), ("old.dump", False, False)])

    def test_resolve_never_leads_out(self):
        touch(self.dir / "a.backup")
        touch(self.root / "secret.backup")
        touch(self.dir / "notes.txt")
        self.assertEqual(backups.resolve(self.dir, "a.backup"), (self.dir / "a.backup").resolve())
        for name in ("../secret.backup", "missing.backup", "notes.txt"):
            with self.assertRaises(backups.BackupError, msg=name):
                backups.resolve(self.dir, name)
        with self.assertRaises(backups.BackupError):
            backups.resolve(None, "a.backup")


class RotateTestCase(SimpleTestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def make(self, prefix, when: datetime):
        path = touch(self.dir / f"{prefix}_{when:%Y-%m-%d_%H%M%S}.backup")
        backups.write_checksum(path)
        return path.name

    def test_generations(self):
        start = datetime(2026, 3, 31, 23, 0)
        hourly = [self.make("coffeepy", start - timedelta(hours=h)) for h in range(24 * 120)]
        touch(self.dir / "handmade.backup")
        keep = SimpleNamespace(keep_recent=5, keep_daily=3, keep_weekly=3, keep_monthly=2)
        deleted = backups.rotate(self.dir, keep)
        left = sorted(p.name for p in self.dir.glob("coffeepy_*.backup"))
        expected = set(hourly[:5])  # the newest five
        expected |= {"coffeepy_2026-03-30_230000.backup", "coffeepy_2026-03-29_230000.backup"}  # one a day
        expected |= {"coffeepy_2026-03-22_230000.backup"}  # the third week (the second is a daily one already)
        expected |= {"coffeepy_2026-02-28_230000.backup"}  # the month before
        self.assertEqual(set(left), expected)
        self.assertEqual(len(deleted), len(hourly) - len(expected))
        self.assertTrue((self.dir / "handmade.backup").exists())  # not ours to delete
        self.assertFalse(any((self.dir / f"{name}.sha256").exists() for name in deleted))

    def test_before_restore_copies_keep_three(self):
        names = [self.make("before-restore", datetime(2026, 1, d)) for d in range(1, 6)]
        backups.rotate(self.dir, SimpleNamespace(keep_recent=1, keep_daily=0, keep_weekly=0, keep_monthly=0))
        self.assertEqual(sorted(p.name for p in self.dir.glob("before-restore_*.backup")), names[2:])


class HealthTestCase(FakeDevice, TestCase):
    def test_states(self):
        (self.storage / "status.json").write_text(json.dumps({"updated": time.time() - 3600}))
        self.assertEqual(backups.health()["state"], "helper")
        self.helper_alive()
        self.assertEqual(backups.health()["state"], "none")
        self.assertFalse(backups.health()["disturbed"])
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "stick-1"}))
        self.assertEqual(backups.health()["state"], "missing")
        self.assertTrue(backups.health()["disturbed"])
        self.set_up_stick()
        self.assertEqual(backups.health()["state"], "ok")
        backups.write_backup_status(last_ok=time.time() - 10, error="")
        self.assertEqual(backups.health()["state"], "ok")
        backups.write_backup_status(last_ok=time.time() - 5 * 3600)
        self.assertEqual(backups.health()["state"], "stale")
        backups.write_backup_status(error="pg_dump: boom")
        self.assertEqual(backups.health()["state"], "failing")

    def test_encryption_is_reported(self):
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": "Kaffee-ist-fertig"}):
            self.assertTrue(backups.health()["encrypted"])
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": ""}):
            self.assertFalse(backups.health()["encrypted"])


class ParseTestCase(SimpleTestCase):
    def test_migrations_from_a_dump(self):
        sql = ("CREATE TABLE x;\nCOPY public.django_migrations (id, app, name, applied) FROM stdin;\n"
               "1\tcoffee\t0001_initial\t2026-01-01\nbroken line\n2\tauth\t0001_initial\t2026-01-01\n\\.\n"
               "COPY public.other FROM stdin;\n1\ta\tb\n\\.\n")
        self.assertEqual(backups.parse_migrations(sql), ["auth.0001_initial", "coffee.0001_initial"])
        self.assertEqual(backups.parse_migrations("nothing here"), [])

    def test_kind_of_a_file(self):
        import tempfile
        cases = {b"PGDMP\x01": "custom", b"--\n-- PostgreSQL database dump\n": "plain"}
        for head, kind in cases.items():
            with tempfile.NamedTemporaryFile() as f:
                f.write(head)
                f.flush()
                self.assertEqual(backups._kind_of(Path(f.name)), kind)
        for head in (b"SQLite format 3\x00", b"CREATE TABLE coffee (x int);", b"garbage"):
            with tempfile.NamedTemporaryFile() as f:
                f.write(head)
                f.flush()
                with self.assertRaises(backups.BackupError):
                    backups._kind_of(Path(f.name))


class OpenBackupTestCase(FakeDevice, TestCase):
    """Reading backups - what doesn't need PostgreSQL."""

    def archive(self, members: dict) -> Path:
        path = self.root / "a.backup"
        with tarfile.open(path, "w") as tar:
            for name, data in members.items():
                source = touch(self.root / "src" / name, data)
                tar.add(source, name)
        return path

    def test_a_tar_without_a_manifest_is_no_backup(self):
        with self.assertRaises(backups.BackupError):
            with backups.open_backup(self.archive({"database.dump": "PGDMP"})):
                pass  # pragma: no cover

    def test_a_device_backup(self):
        path = self.archive({"manifest.json": json.dumps({"migrations": ["coffee.0001_initial"]}),
                             "database.dump": "PGDMP", "config/env": "PUBLIC_HOST=x\n"})
        with backups.open_backup(path) as backup:
            self.assertEqual(backup.migrations(), ["coffee.0001_initial"])
            self.assertEqual(backup.kind, "custom")
            self.assertEqual(backup.config_file("env").read_text(), "PUBLIC_HOST=x\n")
            self.assertIsNone(backup.config_file("device-id"))

    def test_a_device_backup_without_config(self):
        path = self.archive({"manifest.json": "{}", "database.dump": "PGDMP"})
        with backups.open_backup(path) as backup:
            self.assertIsNone(backup.config)
            self.assertIsNone(backup.config_file("env"))
            self.assertEqual(backups.restore_certificates(backup), 0)
            self.assertFalse(backups.restore_identity(backup))
            self.assertEqual(backups.restorable_settings(backup), {})

    def test_an_older_plain_dump(self):
        path = touch(self.root / "old.sql", "-- PostgreSQL database dump\nCOPY public.django_migrations (id, app, name)"
                                            " FROM stdin;\n1\tcoffee\t0001_initial\n\\.\n")
        with backups.open_backup(path) as backup:
            self.assertEqual(backup.kind, "plain")
            backup.check_database()  # nothing to check without pg_restore
            self.assertEqual(backup.migrations(), ["coffee.0001_initial"])
            self.assertIsNone(backup.config_file("env"))

    def test_without_a_storage_directory(self):
        import shutil
        shutil.rmtree(self.storage)
        path = touch(self.root / "old.sql", "-- PostgreSQL database dump\n")
        with backups.open_backup(path) as backup:
            self.assertEqual(backup.kind, "plain")

    def test_an_encrypted_backup_needs_a_passphrase(self):
        path = touch(self.root / "x.backup.enc", "Salted__xxxxxxxx")
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": ""}):
            with self.assertRaises(backups.BackupError) as raised:
                with backups.open_backup(path):
                    pass  # pragma: no cover
        self.assertIn("passphrase", str(raised.exception))

    def test_compatibility(self):
        backup = backups.Backup(database=self.root / "x", kind="custom", manifest={"migrations": ["coffee.0001_initial"]})
        backups.check_compatible(backup)
        backup.manifest["migrations"].append("coffee.9999_from_the_future")
        with self.assertRaises(backups.BackupError):
            backups.check_compatible(backup)

    def test_restoring_config(self):
        path = self.archive({
            "manifest.json": "{}", "database.dump": "PGDMP",
            "config/tls/ca.crt": "CERT", "config/tls/ca.key": "KEY",
            "config/device-id": "device-old\n", "config/stick.json": '{"stick_id": "stick-9"}',
            "config/env": "# comment\nDJANGO_LANGUAGE_CODE=en\nPOSTGRES_PASSWORD=other\nPUBLIC_HOST = 10.0.0.5\nbroken\n",
        })
        with backups.open_backup(path) as backup:
            self.assertEqual(backups.restore_certificates(backup), 2)
            self.assertTrue(backups.restore_identity(backup))
            self.assertEqual(backups.restorable_settings(backup), {"DJANGO_LANGUAGE_CODE": "en", "PUBLIC_HOST": "10.0.0.5"})
        self.assertEqual((self.tls / "ca.crt").read_text(), "CERT")
        self.assertEqual(oct((self.tls / "ca.key").stat().st_mode & 0o777), "0o600")
        self.assertEqual(backups.device_id(), "device-old")
        self.assertEqual(backups.configured_stick(), {"stick_id": "stick-9"})

    def test_config_files_collected(self):
        touch(self.tls / "ca.crt")
        touch(self.tls / ".lock")  # a helper file, not a certificate
        (self.storage / "stick.json").write_text("{}")
        names = [name for name, _ in backups.config_files()]
        self.assertEqual(names, ["config/env", "config/tls/ca.crt", "config/device-id", "config/stick.json"])
        with override_settings(COFFEEPY_ENV_FILE=self.root / "nope", TLS_DIR=self.root / "nope"):
            (self.storage / "device-id").unlink()
            (self.storage / "stick.json").unlink()
            self.assertEqual(backups.config_files(), [])


@needs_openssl
class EncryptionTestCase(FakeDevice, TestCase):
    def test_round_trip_and_wrong_passphrase(self):
        source = touch(self.root / "plain", "the data")
        backups.encrypt(source, self.root / "enc", "right passphrase")
        self.assertTrue((self.root / "enc").read_bytes().startswith(b"Salted__"))
        backups.decrypt(self.root / "enc", self.root / "back", "right passphrase")
        self.assertEqual((self.root / "back").read_text(), "the data")
        with self.assertRaises(backups.BackupError) as raised:
            backups.decrypt(self.root / "enc", self.root / "wrong", "wrong passphrase")
        self.assertIn("wrong passphrase", str(raised.exception))
        self.assertFalse((self.root / "wrong").exists())

    def test_an_encryption_failure_is_not_a_wrong_passphrase(self):
        with self.assertRaises(backups.BackupError) as raised:
            backups.encrypt(self.root / "missing", self.root / "enc", "x")
        self.assertNotIn("passphrase", str(raised.exception))


def seed():
    sort = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
    member = Member.objects.create(uid=1, nickname="Anna")
    Order.objects.create(member=member, coffee_sort=sort, value=Decimal("0.30"), machine_strokes=1)


@needs_postgres
class RealBackupTestCase(FakeDevice, TransactionTestCase):
    """Real dumps, real restores - against the PostgreSQL the tests run on."""

    def setUp(self):
        super().setUp()
        self.target = self.set_up_stick()
        self.target.mkdir()
        touch(self.tls / "ca.crt", "CA")
        seed()

    def restore(self, path, secret=None):
        connections.close_all()  # restore_database cuts every other connection, the test's too
        with backups.open_backup(path, secret) as backup:
            backups.check_compatible(backup)
            backups.restore_database(backup)

    def test_backup_and_restore_round_trip(self):
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": ""}):
            path = backups.new_backup(self.target)
        self.assertTrue(path.name.endswith(".backup"))
        self.assertEqual(backups.verify(path), "ok")
        with tarfile.open(path) as tar:
            self.assertIn("config/env", tar.getnames())
            manifest = json.load(tar.extractfile("manifest.json"))
        self.assertIn("coffee.0001_initial", manifest["migrations"])
        Member.objects.create(uid=2, nickname="Ben")
        self.restore(path)
        self.assertEqual(list(Member.objects.values_list("nickname", flat=True)), ["Anna"])

    def test_encrypted(self):
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": "Kaffee-ist-fertig-42"}):
            path = backups.create_backup(self.target)
            self.assertTrue(path.name.endswith(".backup.enc"))
            self.assertNotIn(b"Anna", path.read_bytes())
            Member.objects.all().delete()
            self.restore(path)
        self.assertTrue(Member.objects.filter(nickname="Anna").exists())
        with mock.patch.dict(os.environ, {"BACKUP_PASSPHRASE": ""}):
            self.restore(path, secret="Kaffee-ist-fertig-42")  # another device's passphrase
            with self.assertRaises(backups.BackupError):
                self.restore(path, secret="wrong")

    def test_a_backup_that_cant_be_read_back_is_removed(self):
        with mock.patch.object(backups.Backup, "check_database", side_effect=backups.BackupError("broken")):
            with self.assertRaises(backups.BackupError):
                backups.create_backup(self.target)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_pg_dump_failing(self):
        with mock.patch.object(backups, "_pg", return_value=(["--host", "nowhere.invalid"], dict(os.environ), "x")):
            with self.assertRaises(backups.BackupError):
                backups.dump(self.root / "x.dump")
        self.assertEqual(list(self.root.glob(".x.dump.part")), [])

    def test_older_dumps_restore_and_report_their_migrations(self):
        backups.dump(self.root / "old.dump")
        with backups.open_backup(self.root / "old.dump") as backup:
            self.assertIn("coffee.0001_initial", backup.migrations())
        Member.objects.all().delete()
        self.restore(self.root / "old.dump")
        self.assertTrue(Member.objects.filter(nickname="Anna").exists())

    def test_a_plain_sql_dump_restores(self):
        args, env, name = backups._pg()
        sql = backups._run(["pg_dump", *args, name], env)
        touch(self.root / "old.sql", sql)
        Member.objects.all().delete()
        self.restore(self.root / "old.sql")
        self.assertTrue(Member.objects.filter(nickname="Anna").exists())

    def test_a_broken_backup_changes_nothing(self):
        touch(self.root / "broken.sql", "-- PostgreSQL database dump\nCREATE TABLE half (id int);\nTHIS IS NOT SQL;\n")
        connections.close_all()
        with self.assertRaises(backups.BackupError) as raised:
            self.restore(self.root / "broken.sql")
        self.assertIn("syntax error", str(raised.exception))
        self.assertTrue(Member.objects.filter(nickname="Anna").exists())

    def test_a_truncated_dump_changes_nothing(self):
        backups.dump(self.root / "full.dump")
        data = (self.root / "full.dump").read_bytes()
        touch(self.root / "cut.dump").write_bytes(data[: len(data) * 2 // 3])
        Member.objects.create(uid=2, nickname="Ben")
        with self.assertRaises(backups.BackupError):
            self.restore(self.root / "cut.dump")
        self.assertEqual(Member.objects.count(), 2)  # rolled back - not half of the backup


@needs_postgres
class BackupCommandTestCase(FakeDevice, TransactionTestCase):
    def test_nothing_is_written_without_the_stick(self):
        call_command("backup_db")  # never set up: nothing to complain about either
        self.assertFalse(AuditLogEntry.objects.exists())
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "stick-1"}))
        call_command("backup_db")
        self.assertEqual(AuditLogEntry.objects.get().outcome, "failure")
        self.assertIn("No backup stick", backups.read_backup_status()["skipped"])
        with self.assertRaises(CommandError):
            call_command("backup_db", once=True)

    def test_backs_up_when_due(self):
        target = self.set_up_stick()
        call_command("backup_db", if_due=True)
        self.assertEqual(len([p for p in target.iterdir() if backups.is_backup_name(p.name)]), 1)
        self.assertEqual(AuditLogEntry.objects.get(action="backup.automatic").outcome, "success")
        call_command("backup_db", if_due=True)  # not due again yet
        self.assertEqual(len([p for p in target.iterdir() if backups.is_backup_name(p.name)]), 1)
        status = backups.read_backup_status()
        (self.storage / "backup-status.json").write_text(json.dumps({**status, "attempted": time.time() - 3601}))
        call_command("backup_db", if_due=True)  # an hour later: due
        self.assertEqual(len([p for p in target.iterdir() if backups.is_backup_name(p.name)]), 2)
        self.assertEqual(backups.health()["state"], "ok")

    def test_by_hand_is_not_logged_as_automatic(self):
        target = self.set_up_stick()
        call_command("backup_db", once=True)
        call_command("backup_db", once=True)  # twice in a second: two files, none overwritten
        self.assertEqual(len([p for p in target.iterdir() if backups.is_backup_name(p.name)]), 2)
        self.assertFalse(AuditLogEntry.objects.filter(action="backup.automatic").exists())

    def test_a_failing_backup_is_reported(self):
        self.set_up_stick()
        with mock.patch.object(backups, "new_backup", side_effect=backups.BackupError("disk full")):
            with self.assertRaises(CommandError):
                call_command("backup_db")
        self.assertEqual(backups.read_backup_status()["error"], "disk full")
        self.assertEqual(backups.health()["state"], "failing")

    def test_settings_start_from_the_installation(self):
        with mock.patch.dict(os.environ, {"BACKUP_INTERVAL_MINUTES": "30", "BACKUP_DEPTH": "notanumber"}):
            settings = BackupSettings.current()
        self.assertEqual((settings.interval_minutes, settings.keep_recent), (30, 48))


class FakeProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b"", broken_after=None, running=False):
        import io
        self.returncode, self._running = returncode, running
        self.stdout, self.stderr = io.BytesIO(stdout), io.BytesIO(stderr)
        self.written, self.broken_after, self.killed = b"", broken_after, False
        self.stdin = self

    def write(self, data):
        if self.broken_after is not None and len(self.written) >= self.broken_after:
            raise BrokenPipeError
        self.written += data

    def close(self):
        pass

    def wait(self):
        self._running = False
        return self.returncode

    def poll(self):
        return None if self._running else self.returncode

    def kill(self):
        self.killed = True


class RestoreBranchesTestCase(SimpleTestCase):
    """The ways a restore can go wrong that real files can't reliably produce - each must end
    rolled back, never committed."""

    def run_restore(self, restore, psql):
        backup = backups.Backup(database=Path("/dev/null"), kind="custom")
        with mock.patch.object(backups, "_run"), \
                mock.patch.object(backups.subprocess, "Popen", side_effect=[restore, psql]):
            backups.restore_database(backup)

    def test_pg_restore_failing_half_way_rolls_back(self):
        restore = FakeProcess(returncode=1, stdout=b"CREATE TABLE a (id int);\n", stderr=b"pg_restore: error: bad block")
        psql = FakeProcess()
        with self.assertRaises(backups.BackupError) as raised:
            self.run_restore(restore, psql)
        self.assertIn("bad block", str(raised.exception))
        self.assertTrue(psql.written.endswith(b"ROLLBACK;\n"))
        self.assertNotIn(b"COMMIT", psql.written)

    def test_psql_stopping_at_an_error(self):
        restore = FakeProcess(stdout=b"line\n" * 10, running=True)
        psql = FakeProcess(returncode=3, stderr=b"ERROR: boom", broken_after=0)
        with self.assertRaises(backups.BackupError) as raised:
            self.run_restore(restore, psql)
        self.assertIn("boom", str(raised.exception))
        self.assertTrue(restore.killed)  # nothing left running

    def test_the_server_never_sees_settings_it_doesnt_know(self):
        restore = FakeProcess(stdout=b"SET transaction_timeout = 0;\nCREATE TABLE a (id int);\n")
        psql = FakeProcess()
        self.run_restore(restore, psql)
        self.assertNotIn(b"transaction_timeout", psql.written)
        self.assertTrue(psql.written.endswith(b"COMMIT;\n"))
