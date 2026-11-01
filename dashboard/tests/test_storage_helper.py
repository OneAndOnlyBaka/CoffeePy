"""install/storage_helper.py against a simulated host: block devices from a model instead of
lsblk, a mount is a symlink from the mount point to the stick's directory. Every path the
helper takes - setting up, pulling out, plugging back in, another device's stick, adopting,
ejecting, a restored identity, settings, SSH - without root and without a device."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase


def load_helper(project, usb_root):
    with mock.patch.dict(os.environ, {"COFFEEPY_DIR": str(project), "COFFEEPY_USB_ROOT": str(usb_root)}):
        spec = importlib.util.spec_from_file_location("storage_helper", settings.BASE_DIR / "install" / "storage_helper.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class FakeHost:
    """The block devices and mounts the helper sees, and the commands it runs on them."""

    def __init__(self, root: Path):
        self.root = root
        self.disks = {}  # path -> {"serial", "children": {part path: volume dir}, ...}
        self.volumes = {}  # volume path -> {"dir": Path, "label", "fstype", "uuid"}
        self.mounted = {}  # mount point -> volume path
        self.commands = []
        self.fail = set()  # command names that fail
        self.new_partition = True
        self.ssh = set()  # the SSH units that run

    # --- the model ---
    def plug(self, path, serial="S1", vendor="SanDisk", model="Cruzer", label="", fstype="vfat", partitioned=True):
        volume = f"{path}1" if partitioned else path
        directory = self.root / "sticks" / Path(volume).name
        directory.mkdir(parents=True, exist_ok=True)
        self.disks[path] = {"serial": serial, "vendor": vendor, "model": model, "partitioned": partitioned}
        self.volumes[volume] = {"dir": directory, "label": label, "fstype": fstype, "uuid": f"uuid-{Path(volume).name}"}
        return directory

    def pull(self, path):
        self.disks.pop(path)
        for volume in [v for v in self.volumes if v.startswith(path)]:
            self.volumes.pop(volume)

    def lsblk(self):
        devices = [{"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "tran": "mmc", "size": 64,
                    "children": [{"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "type": "part", "fstype": "ext4"}]}]
        for path, disk in self.disks.items():
            entry = {"name": Path(path).name, "path": path, "type": "disk", "tran": "usb", "size": 16 << 30,
                     "serial": disk["serial"], "vendor": disk["vendor"] + "  ", "model": disk["model"]}
            volumes = {v: d for v, d in self.volumes.items() if v.startswith(path)}
            if disk["partitioned"]:
                entry["children"] = [{"name": Path(v).name, "path": v, "type": "part", "label": d["label"],
                                      "uuid": d["uuid"], "fstype": d["fstype"], "size": 16 << 30} for v, d in volumes.items()]
            else:
                entry.update(label=volumes[path]["label"], uuid=volumes[path]["uuid"], fstype=volumes[path]["fstype"])
            devices.append(entry)
        return devices

    def mounts(self):
        return {"/": "/dev/mmcblk0p2", **{str(target): volume for target, volume in self.mounted.items()}}

    # --- the commands ---
    def run(self, *args, stdin=None):
        self.commands.append(args)
        name = args[0]
        if name in self.fail:
            raise RuntimeError(f"{name}: failed")
        if name == "mount":
            volume, target = args[-2], Path(args[-1])
            if volume not in self.volumes:
                raise RuntimeError("mount: no such device")
            shutil.rmtree(target, ignore_errors=True)
            target.unlink(missing_ok=True)
            target.symlink_to(self.volumes[volume]["dir"], target_is_directory=True)
            self.mounted[target] = volume
        elif name == "umount":
            target = Path(args[-1])
            self.mounted.pop(target, None)
            if target.is_symlink():
                target.unlink()
                target.mkdir()
        elif name == "sfdisk":
            disk = args[-1]
            self.disks[disk]["partitioned"] = True
            for volume in [v for v in self.volumes if v.startswith(disk)]:
                self.volumes.pop(volume)
            if self.new_partition:
                directory = self.root / "sticks" / (Path(disk).name + "1")
                shutil.rmtree(directory, ignore_errors=True)
                directory.mkdir(parents=True)
                self.volumes[disk + "1"] = {"dir": directory, "label": "", "fstype": "", "uuid": "uuid-new"}
        elif name == "mkfs.ext4":
            volume = args[-1]
            self.volumes[volume].update(label="COFFEEBAK", fstype="ext4")
        elif args[:3] == ("systemctl", "enable", "--now"):
            self.ssh.add(args[-1])
        elif args[:3] == ("systemctl", "disable", "--now"):
            self.ssh.discard(args[-1])
        elif args[:2] == ("systemctl", "is-active"):
            return "".join("active\n" if unit in self.ssh else "inactive\n" for unit in args[2:])
        return ""

    def subprocess_run(self, args, **kwargs):
        try:
            return subprocess.CompletedProcess(args, 0, self.run(*args), "")
        except RuntimeError as exc:  # what the helper's run() raises on, plain subprocess.run reports
            return subprocess.CompletedProcess(args, 1, "", str(exc))


class StorageHelperTestCase(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.project, self.usb_root = self.root / "project", self.root / "usb"
        self.project.mkdir()
        self.helper = load_helper(self.project, self.usb_root)
        self.host = FakeHost(self.root)
        for name, replacement in (("run", self.host.run), ("lsblk", self.host.lsblk), ("mounts", self.host.mounts)):
            patcher = mock.patch.object(self.helper, name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.helper.subprocess, "run", side_effect=self.host.subprocess_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.helper.ejected.clear()
        self.storage = self.project / "data" / "storage"
        for directory in (self.helper.REQUESTS_DIR, self.helper.OWN, self.helper.FOREIGN):
            directory.mkdir(parents=True, exist_ok=True)
        self.last = {}

    def cycle(self):
        self.helper.cycle(self.last)
        return json.loads((self.storage / "status.json").read_text())

    def request(self, action, **fields):
        (self.helper.REQUESTS_DIR / f"{action}.json").write_text(json.dumps({"id": action, "action": action, **fields}))
        return self.cycle()

    def events(self):
        path = self.storage / "events.jsonl"
        return [json.loads(line)["action"] for line in path.read_text().splitlines()] if path.exists() else []

    def set_up(self):
        self.host.plug("/dev/sda", serial="S1")
        self.request("format", path="/dev/sda", serial="S1", requested_by="boss")
        return self.helper.read_meta(self.helper.OWN)

    def test_a_new_stick_is_offered_not_touched(self):
        self.host.plug("/dev/sda", partitioned=False)
        status = self.cycle()
        self.assertEqual([d["path"] for d in status["available"]], ["/dev/sda"])
        self.assertEqual(status["available"][0]["vendor"], "SanDisk")
        self.assertIsNone(status["own"])
        self.assertIsNone(status["configured"])
        self.assertTrue(status["device_id"])  # created on first sight

    def test_setting_up_a_stick(self):
        meta = self.set_up()
        status = json.loads((self.storage / "status.json").read_text())
        self.assertEqual(status["request"]["state"], "done")
        self.assertEqual(status["own"]["volume"], "/dev/sda1")
        self.assertEqual(status["own"]["model"], "SanDisk Cruzer")
        self.assertEqual(meta["device_id"], self.helper.device_id())
        self.assertEqual(json.loads((self.storage / "stick.json").read_text())["stick_id"], meta["stick_id"])
        self.assertTrue((self.helper.OWN / "backups").is_dir())
        self.assertIn("backup.stick_format", self.events())
        self.assertEqual(status["available"], [])

    def test_formatting_refuses_another_stick_or_a_vanished_one(self):
        self.host.plug("/dev/sda", serial="S1")
        status = self.request("format", path="/dev/sda", serial="OTHER")
        self.assertIn("different stick", status["request"]["message"])
        status = self.request("format", path="/dev/sdz", serial="S1")
        self.assertIn("no longer there", status["request"]["message"])
        self.assertFalse(any(c[0] == "wipefs" for c in self.host.commands))

    def test_formatting_without_a_new_partition(self):
        self.host.plug("/dev/sda", serial="S1")
        self.host.new_partition = False
        status = self.request("format", path="/dev/sda", serial="S1")
        self.assertIn("did not show up", status["request"]["message"])

    def test_formatting_unmounts_what_is_mounted_from_the_stick_and_the_old_stick(self):
        old = self.set_up()
        self.host.plug("/dev/sdb", serial="S2", label="COFFEEBAK", fstype="ext4")
        self.host.mounted[self.helper.FOREIGN] = "/dev/sdb1"  # mounted from earlier
        self.request("format", path="/dev/sdb", serial="S2")
        new = self.helper.read_meta(self.helper.OWN)
        self.assertNotEqual(old["stick_id"], new["stick_id"])
        self.assertEqual(self.host.mounted[self.helper.OWN], "/dev/sdb1")

    def test_pulled_out_and_plugged_back_in(self):
        self.set_up()
        self.host.pull("/dev/sda")
        status = self.cycle()
        self.assertIsNone(status["own"])
        self.assertIsNotNone(status["configured"])
        self.assertIn("backup.stick_removed", self.events())
        directory = self.root / "sticks" / "sda1"
        self.host.disks["/dev/sda"] = {"serial": "S1", "vendor": "SanDisk", "model": "Cruzer", "partitioned": True}
        self.host.volumes["/dev/sda1"] = {"dir": directory, "label": "COFFEEBAK", "fstype": "ext4", "uuid": "uuid-new"}
        status = self.cycle()
        self.assertEqual(status["own"]["volume"], "/dev/sda1")
        self.assertIn("backup.stick_mounted", self.events())

    def test_another_devices_stick_is_read_only_until_adopted(self):
        directory = self.host.plug("/dev/sdb", serial="S2", label="COFFEEBAK", fstype="ext4")
        (directory / self.helper.META).write_text(json.dumps({"device_id": "kitchen", "stick_id": "k1",
                                                              "previous_device_ids": ["older"]}))
        status = self.cycle()
        self.assertEqual(status["foreign"]["volume"], "/dev/sdb1")
        self.assertIn(("mount", "-o", "ro,nosuid,nodev,noexec", "/dev/sdb1", str(self.helper.FOREIGN)), self.host.commands)
        self.cycle()  # stays where it is
        status = self.request("adopt", requested_by="boss")
        self.assertEqual(status["request"]["state"], "done")
        meta = self.helper.read_meta(self.helper.OWN)
        self.assertEqual(meta["previous_device_ids"], ["kitchen", "older"])
        self.assertEqual(status["own"]["volume"], "/dev/sdb1")
        self.assertIsNone(status["foreign"])

    def test_adopting_needs_a_foreign_stick_and_replaces_the_own(self):
        status = self.request("adopt")
        self.assertIn("no other CoffeePy stick", status["request"]["message"])
        self.set_up()
        directory = self.host.plug("/dev/sdb", serial="S2", label="COFFEEBAK", fstype="ext4")
        (directory / self.helper.META).write_text(json.dumps({"device_id": "kitchen", "stick_id": "k1"}))
        self.cycle()
        self.request("adopt")
        self.assertEqual(self.host.mounted[self.helper.OWN], "/dev/sdb1")

    def test_ejected_stays_out_until_pulled(self):
        self.set_up()
        status = self.request("eject")
        self.assertIsNone(status["own"])
        self.assertIsNone(self.cycle()["own"])  # not remounted while still plugged in
        self.host.pull("/dev/sda")
        self.cycle()
        self.assertEqual(self.helper.ejected, set())
        status = self.request("eject")
        self.assertIn("No stick is mounted", status["request"]["message"])

    def test_ejecting_a_mount_whose_disk_is_unknown(self):
        self.host.mounted[self.helper.OWN] = "/dev/sdx1"
        (self.helper.OWN).mkdir(exist_ok=True)
        disks = []
        self.assertEqual(self.helper.do_eject({}, disks, self.host.mounts()), "ejected")

    def test_a_restored_identity_brings_the_old_stick_back(self):
        directory = self.host.plug("/dev/sdb", serial="S2", label="COFFEEBAK", fstype="ext4")
        (directory / self.helper.META).write_text(json.dumps({"device_id": "old-device", "stick_id": "old-stick"}))
        self.assertEqual(self.cycle()["foreign"]["volume"], "/dev/sdb1")
        (self.storage / "device-id").write_text("old-device\n")
        (self.storage / "stick.json").write_text(json.dumps({"stick_id": "old-stick"}))
        status = self.cycle()
        self.assertEqual(status["own"]["volume"], "/dev/sdb1")
        self.assertIsNone(status["foreign"])

    def test_a_second_own_stick_waits(self):
        self.set_up()
        directory = self.host.plug("/dev/sdc", serial="S3", label="COFFEEBAK", fstype="ext4")
        (directory / self.helper.META).write_text(json.dumps(self.helper.read_meta(self.helper.OWN)))
        self.cycle()
        self.assertEqual(self.host.mounted[self.helper.OWN], "/dev/sda1")

    def test_sticks_it_cant_use(self):
        self.host.plug("/dev/sda", label="COFFEEBAK", fstype="ext4")  # labelled, but no metadata
        self.host.plug("/dev/sdb", label="OTHER", fstype="ext4")
        self.cycle()
        self.assertEqual(self.host.mounted, {})
        self.host.fail.add("mount")
        self.assertIsNone(self.helper.probe_meta({"path": "/dev/sda1"}))

    def test_a_mount_that_fails(self):
        directory = self.host.plug("/dev/sdb", serial="S2", label="COFFEEBAK", fstype="ext4")
        (directory / self.helper.META).write_text(json.dumps({"device_id": "kitchen", "stick_id": "k1"}))
        real_run = self.host.run

        def flaky(*args, **kwargs):
            if args[0] == "mount" and args[-1] == str(self.helper.FOREIGN):
                raise RuntimeError("mount: busy")
            return real_run(*args, **kwargs)
        self.helper.run.side_effect = flaky
        self.assertIsNone(self.cycle()["foreign"])

    def test_settings_from_a_restored_backup(self):
        env = self.project / ".env"
        env.write_text("# comment\nPUBLIC_HOST=192.168.4.1\nPOSTGRES_PASSWORD=keep\n")
        status = self.request("apply_settings", values={
            "PUBLIC_HOST": "10.0.0.5", "DJANGO_LANGUAGE_CODE": "en", "POSTGRES_PASSWORD": "stolen", "DJANGO_TIME_ZONE": "a\nb"})
        self.assertEqual(status["request"]["state"], "done")
        self.assertEqual(env.read_text(), "# comment\nPUBLIC_HOST=10.0.0.5\nPOSTGRES_PASSWORD=keep\nDJANGO_LANGUAGE_CODE=en\n")
        self.assertEqual(oct(env.stat().st_mode & 0o777), "0o600")
        self.assertIn(("docker", "compose", "--project-directory", str(self.project), "up", "-d"), self.host.commands)
        env.unlink()
        self.request("apply_settings", values={"PUBLIC_HOST": "x"})
        self.assertEqual(env.read_text(), "PUBLIC_HOST=x\n")
        status = self.request("apply_settings", values={"POSTGRES_PASSWORD": "x"})
        self.assertIn("No settings", status["request"]["message"])

    def test_the_settings_lists_agree(self):
        from coffee import backups
        self.assertEqual(self.helper.SETTINGS_KEYS, backups.RESTORABLE_SETTINGS)
        # ...and with .env: both once said TIME_ZONE where .env has DJANGO_TIME_ZONE, so no
        # backup ever carried the time zone - two lists agreeing on a wrong name.
        example = (settings.BASE_DIR / ".env.example").read_text()
        for key in backups.RESTORABLE_SETTINGS:
            self.assertRegex(example, rf"(?m)^#? ?{key}=", key)

    def test_requests_it_cant_read_or_doesnt_know(self):
        (self.helper.REQUESTS_DIR / "broken.json").write_text("{nope")
        status = self.cycle()
        self.assertEqual(list(self.helper.REQUESTS_DIR.iterdir()), [])
        self.assertEqual(status["request"], {})
        status = self.request("explode")
        self.assertIn("Unknown request", status["request"]["message"])

    def test_ssh_on_and_off(self):
        self.host.ssh = {"ssh.socket"}  # an image that starts it on demand
        status = self.cycle()
        self.assertTrue(status["ssh"]["active"])
        status = self.request("ssh", enabled=False, requested_by="boss")
        self.assertEqual((status["ssh"]["active"], status["ssh"]["error"]), (False, ""))
        self.assertEqual(status["request"], {})  # not the stick's: the backup page shows nothing
        status = self.request("ssh", enabled=True, requested_by="boss")
        self.assertTrue(status["ssh"]["active"])
        self.assertEqual(self.host.ssh, {"ssh.service"})
        self.assertEqual(self.events(), ["ssh.disable", "ssh.enable"])

    def test_ssh_state_is_asked_for_only_now_and_then(self):
        self.cycle()
        self.host.ssh = {"ssh.service"}
        self.assertFalse(self.cycle()["ssh"]["active"])
        self.helper.ssh["checked"] -= self.helper.SSH_SECONDS
        self.assertTrue(self.cycle()["ssh"]["active"])

    def test_ssh_that_cant_be_switched(self):
        self.host.fail.add("systemctl")
        status = self.request("ssh", enabled=True)
        self.assertIn("systemctl: failed", status["ssh"]["error"])
        lines = [json.loads(line) for line in (self.storage / "events.jsonl").read_text().splitlines()]
        self.assertEqual([(e["action"], e["outcome"]) for e in lines], [("ssh.enable", "failure")])

    def test_mounted_info_for_a_disk_it_no_longer_lists(self):
        self.host.mounted[self.helper.OWN] = "/dev/gone1"
        info = self.helper.mounted_info(self.helper.OWN, self.host.mounts(), [])
        self.assertEqual((info["disk"], info["model"], info["serial"]), ("", "", ""))

    def test_main_keeps_going_after_a_failed_cycle(self):
        self.helper.lsblk.side_effect = RuntimeError("lsblk: gone")
        self.assertEqual(self.helper.main(once=True), 0)
        self.helper.lsblk.side_effect = self.host.lsblk
        with mock.patch.object(self.helper.time, "sleep", side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                self.helper.main()


class StorageHelperPiecesTestCase(SimpleTestCase):
    """The parts that touch the real system - small enough to try for real."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.helper = load_helper(self.root, self.root / "usb")

    def test_run(self):
        self.assertEqual(self.helper.run("echo", "hi"), "hi\n")
        with self.assertRaises(RuntimeError):
            self.helper.run("sh", "-c", "echo bad >&2; exit 1")

    def test_mounts_and_space(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="/dev/sda1 /srv/my\\040stick ext4 rw 0 0\n")):
            self.assertEqual(self.helper.mounts(), {"/srv/my stick": "/dev/sda1"})
        self.assertIsNone(self.helper.space(self.root / "missing"))
        self.assertGreater(self.helper.space(self.root)["total"], 0)

    def test_lsblk(self):
        with mock.patch.object(self.helper, "run", return_value='{"blockdevices": [{"name": "sda"}]}'):
            self.assertEqual(self.helper.lsblk(), [{"name": "sda"}])

    def test_events_survive_a_full_disk(self):
        with mock.patch("builtins.open", side_effect=OSError("no space")):
            self.helper.event("backup.stick_mounted")  # logged, not raised

    def test_is_own(self):
        self.assertFalse(self.helper.is_own({"device_id": "a", "stick_id": "s"}, "a"))  # nothing set up
        (self.root / "data" / "storage").mkdir(parents=True)
        (self.root / "data" / "storage" / "stick.json").write_text('{"stick_id": "s"}')
        self.assertTrue(self.helper.is_own({"device_id": "a", "stick_id": "s"}, "a"))
        self.assertFalse(self.helper.is_own(None, "a"))
