import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase


@skipUnless(shutil.which("rsync"), "needs rsync")
class InstallerCopyTestCase(SimpleTestCase):
    """install.sh's app step copies the checkout over /opt/coffeepy with rsync --delete-after
    and install/src-filter.txt. Whatever the device keeps under data/ must survive that - a
    re-run once deleted the update watcher's rollback snapshot and its saved databases."""

    DEVICE_STATE = (
        "data/updates/installed.json", "data/updates/previous.json", "data/updates/state.json",
        "data/updates/docker-compose.yml.previous", "data/updates/install.previous/install.sh",
        "data/updates/pre-update/before-v1.dump", "data/updates/system.json", "data/updates/incoming/x.tar",
        "data/storage/device-id", "data/storage/stick.json", "data/tls/ca.crt", "data/maintenance/status.json",
        "data/backups/old.dump", ".env",
    )

    def test_a_re_run_keeps_the_devices_state(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        source, target = root / "checkout", root / "opt"
        (source / "install").mkdir(parents=True)
        shutil.copy(settings.BASE_DIR / "install" / "src-filter.txt", source / "install")
        (source / "manage.py").write_text("new\n")
        (source / ".env").write_text("a developer's own\n")
        for name in self.DEVICE_STATE + ("gone.py",):
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("device\n")
        subprocess.run(["rsync", "-a", "--delete-after", f"--filter=. {source}/install/src-filter.txt",
                        f"{source}/", f"{target}/"], check=True)
        for name in self.DEVICE_STATE:
            self.assertEqual((target / name).read_text(), "device\n", name)
        self.assertEqual((target / "manage.py").read_text(), "new\n")
        self.assertFalse((target / "gone.py").exists())  # the code itself is replaced
