"""A fake device for the backup and update tests: STORAGE_DIR, USB_DIR, TLS_DIR, .env,
the maintenance and update directories - all in a temporary directory, the way the host's
storage helper and update watcher fill them. And whether the real PostgreSQL tools are
there (scripts/test.sh runs the suite in a container that has them)."""
import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.db import connection
from django.test import override_settings


def has_postgres_tools() -> bool:
    return connection.vendor == "postgresql" and all(shutil.which(t) for t in ("pg_dump", "pg_restore", "psql", "openssl"))


needs_postgres = skipUnless(has_postgres_tools(), "needs PostgreSQL and its tools - run scripts/test.sh")
needs_openssl = skipUnless(shutil.which("openssl"), "needs openssl")


class FakeDevice:
    """Mix into a TestCase: self.root, self.storage, self.usb, self.tls, self.env_file."""

    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.storage, self.usb, self.tls = self.root / "storage", self.root / "usb", self.root / "tls"
        for directory in (self.storage, self.usb / "backup", self.usb / "foreign", self.tls):
            directory.mkdir(parents=True)
        (self.storage / "device-id").write_text("device-a\n")
        self.env_file = self.root / "env"
        self.env_file.write_text("DJANGO_LANGUAGE_CODE=de\nPOSTGRES_PASSWORD=secret\nPUBLIC_HOST=192.168.4.1\n")
        overrides = override_settings(
            STORAGE_DIR=self.storage, USB_DIR=self.usb, TLS_DIR=self.tls, COFFEEPY_ENV_FILE=self.env_file,
            RESTORE_STATUS_FILE=self.root / "restore.json", MAINTENANCE_DIR=self.root / "maintenance",
            UPDATE_DIR=self.root / "updates", UPDATE_INCOMING_DIR=self.root / "updates" / "incoming",
            UPDATE_STATUS_DIR=self.root / "updates" / "status",
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.helper_alive()

    def helper_alive(self, **fields):
        (self.storage / "status.json").write_text(json.dumps({"updated": time.time(), "available": [], **fields}))

    def set_up_stick(self, stick_id="stick-1", device="device-a", where="backup"):
        """A stick mounted at `where`, and stick.json naming stick-1 as this device's."""
        (self.usb / where / ".coffeepy-backup.json").write_text(json.dumps(
            {"device_id": device, "stick_id": stick_id, "device_name": "coffeepy", "set_up_at": "2026-01-01T00:00:00"}))
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "stick-1", "model": "SanDisk Cruzer",
                                                             "serial": "ABC"}))
        return self.usb / where / "backups"
