"""install/update_watcher.py against a simulated device: real bundles (tar, manifest, Ed25519
signature checked by the real openssl), docker and compose replaced by a model that records
what was done - so every check, the update, the automatic and the manual rollback run
without Docker."""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.conf import settings
from django.test import SimpleTestCase

HAS_OPENSSL = bool(shutil.which("openssl"))


def load_watcher(project, pubkey):
    env = {"COFFEEPY_DIR": str(project), "COFFEEPY_UPDATE_PUBKEY": str(pubkey)}
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("update_watcher", settings.BASE_DIR / "install" / "update_watcher.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class FakeDocker:
    """docker / docker compose / install.sh as the watcher calls them."""

    def __init__(self, project: Path, error):
        self.project, self.error = project, error
        self.calls = []
        self.images = ["coffeepy-web:latest", "postgres:16-alpine"]
        self.tags = {}
        self.restored = []
        self.fail = set()

    def run(self, *args, stdin=None, cwd=None, check=True):
        args = tuple(str(a) for a in args)
        if args[0] == "openssl":  # the signature check is real
            real = subprocess.run(args, input=stdin, capture_output=True)
            if check and real.returncode != 0:
                raise self.error(f"openssl: {real.stderr.decode()}")
            return real
        self.calls.append(args)
        key = " ".join(args[:4])
        for failing in self.fail:
            if failing in " ".join(args):
                if check:
                    raise self.error(f"{failing} failed")
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"failed")
        stdout = b""
        if args[:2] == ("docker", "compose") and "config" in args:
            stdout = "\n".join(self.images).encode()
        elif args[:2] == ("docker", "compose") and "pg_dump" in args:
            stdout = b"PGDMP-dump"
        elif args[:2] == ("docker", "compose") and args[2:5] == ("exec", "-T", "db") and "sh" in args:
            self.restored.append(stdin)
        elif args[:2] == ("docker", "tag"):
            self.tags[args[3]] = args[2]
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"", key=key)


@skipUnless(HAS_OPENSSL, "needs openssl")
class UpdateWatcherTestCase(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.project = self.root / "opt"
        (self.project / "install").mkdir(parents=True)
        (self.project / "install" / "install.sh").write_text("#!/bin/sh\n")
        (self.project / "docker-compose.yml").write_text("services: {old: {}}\n")
        (self.project / ".env").write_text("# settings\nPOSTGRES_USER=coffee\nDJANGO_LANGUAGE_CODE=de\n")
        (self.project / "data" / "tls").mkdir(parents=True)
        self.key, self.pubkey = self.root / "key.pem", self.root / "pub.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(self.key)], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.key), "-pubout", "-out", str(self.pubkey)], check=True)
        self.watcher = load_watcher(self.project, self.pubkey)
        self.docker = FakeDocker(self.project, self.watcher.UpdateError)
        self.patch(self.watcher, "run", self.docker.run)
        self.patch(self.watcher, "healthy", lambda timeout=None: self.health.pop(0) if self.health else True)
        self.patch(self.watcher, "host_platform", lambda: "linux/arm64")
        self.health = []
        for directory in (self.watcher.INCOMING, self.watcher.STATUS_DIR, self.watcher.PRE_UPDATE):
            directory.mkdir(parents=True, exist_ok=True)

    def patch(self, target, name, replacement):
        patcher = mock.patch.object(target, name, side_effect=replacement)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bundle(self, name="coffeepy-update-v2.tar", version="v2", built_at=None, platform="linux/arm64",
               install=True, os_packages=False, sign=True, key=None, tamper=False, manifest_extra=None,
               files=None):
        work = Path(tempfile.mkdtemp(dir=self.root, prefix="build-"))
        (work / "images.tar").write_bytes(b"IMAGES")
        (work / "docker-compose.yml").write_text("services: {new: {}}\n")
        if install:
            source = work / "src" / "install"
            source.mkdir(parents=True)
            (source / "install.sh").write_text("#!/bin/sh\necho new\n")
            (source / "update_watcher.py").write_text("# new\n")
            with tarfile.open(work / "install.tar", "w") as tar:
                tar.add(source, "install")
        if os_packages:
            with tarfile.open(work / "os.tar", "w") as tar:
                (work / "Packages").write_text("")
                tar.add(work / "Packages", "debs/Packages")
        members = files or [n for n in ("images.tar", "docker-compose.yml", "install.tar", "os.tar") if (work / n).exists()]
        digests = {n: hashlib.sha256((work / n).read_bytes()).hexdigest() for n in members}
        manifest = {"format": 2, "version": version, "built_at": built_at or time.time(), "platform": platform,
                    "required_mb": 1, "env_defaults": {"AUDIT_RETENTION_DAYS": "90", "DJANGO_LANGUAGE_CODE": "en"},
                    "files": digests, **(manifest_extra or {})}
        (work / "manifest.json").write_text(json.dumps(manifest))
        if sign:
            subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key or self.key), "-rawin",
                            "-in", str(work / "manifest.json"), "-out", str(work / "manifest.sig")], check=True)
        if tamper:
            (work / "images.tar").write_bytes(b"OTHER IMAGES")
        path = self.watcher.INCOMING / name
        with tarfile.open(path, "w") as tar:
            for member in ["manifest.json", "manifest.sig", *members]:
                if (work / member).exists():
                    tar.add(work / member, member)
        return path

    def state(self):
        return json.loads(self.watcher.STATE_FILE.read_text())

    def apply(self, bundle, options=None):
        if options is not None:
            bundle.with_name(bundle.name + ".options.json").write_text(json.dumps(options))
        self.watcher.process_bundle(bundle)
        return self.state()

    def events(self):
        path = self.project / "data" / "storage" / "events.jsonl"
        return [(json.loads(l)["action"], json.loads(l)["outcome"]) for l in path.read_text().splitlines()]

    # --- the good path ---

    def test_an_update(self):
        bundle = self.bundle()
        state = self.apply(bundle, {"requested_by": "boss"})
        self.assertEqual(state["state"], "done", state)
        self.assertEqual(json.loads(self.watcher.INSTALLED_FILE.read_text())["version"], "v2")
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {new: {}}\n")
        self.assertEqual((self.project / "install" / "update_watcher.py").read_text(), "# new\n")
        env = (self.project / ".env").read_text()
        self.assertIn("AUDIT_RETENTION_DAYS=90", env)
        self.assertIn("DJANGO_LANGUAGE_CODE=de", env)  # what the device has is never changed
        self.assertNotIn("DJANGO_LANGUAGE_CODE=en", env)
        self.assertEqual(self.docker.tags, {"coffeepy-web:coffeepy-previous-latest": "coffeepy-web:latest",
                                            "postgres:coffeepy-previous-16-alpine": "postgres:16-alpine"})
        self.assertEqual(len(list(self.watcher.PRE_UPDATE.glob("*.dump"))), 1)
        self.assertIn(("docker", "load", "-i"), [c[:3] for c in self.docker.calls])
        self.assertIn(str(self.project / "install" / "install.sh"), [c[0] for c in self.docker.calls])
        self.assertIn(("systemctl", "restart", "--no-block", "coffeepy-update-watcher.service"), self.docker.calls)
        self.assertFalse(bundle.exists())
        self.assertFalse(self.watcher.MAINTENANCE_FILE.exists())
        self.assertEqual(self.events(), [("update.start", "success"), ("update.apply", "success")])
        self.assertTrue(json.loads(self.watcher.PREVIOUS_FILE.read_text())["images"])

    def test_with_os_packages_without_install(self):
        state = self.apply(self.bundle(install=False, os_packages=True))
        self.assertEqual(state["state"], "done", state)
        installer = [c for c in self.docker.calls if c[0].endswith("install.sh")]
        self.assertEqual(installer[0][1], "--only=packages")
        self.assertEqual(len(installer), 1)  # no services step without install/
        self.assertNotIn(("systemctl", "restart", "--no-block", "coffeepy-update-watcher.service"), self.docker.calls)

    def test_a_device_without_install_dir_and_nothing_new_in_env(self):
        shutil.rmtree(self.project / "install")
        with open(self.project / ".env", "a") as f:
            f.write("AUDIT_RETENTION_DAYS=30\n")
        state = self.apply(self.bundle(install=False))
        self.assertEqual(state["state"], "done", state)
        self.assertNotIn("Added by update", (self.project / ".env").read_text())
        self.assertFalse((self.watcher.UPDATES / "install.previous").exists())

    def test_only_the_newest_pre_update_dumps_are_kept(self):
        for k in range(4):
            dump = self.watcher.PRE_UPDATE / f"old{k}.dump"
            dump.write_bytes(b"x")
            os.utime(dump, (k, k))
        self.apply(self.bundle())
        self.assertEqual(len(list(self.watcher.PRE_UPDATE.glob("*.dump"))), self.watcher.KEEP_PRE_UPDATE)

    # --- refused before anything changes ---

    def assert_refused(self, state, text):
        self.assertEqual(state["state"], "failed", state)
        self.assertIn(text, state["message"])
        self.assertEqual(self.docker.calls, [])  # nothing touched
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")

    def test_unsigned(self):
        self.assert_refused(self.apply(self.bundle(sign=False)), "not signed")

    def test_unsigned_allowed_on_this_device(self):
        with open(self.project / ".env", "a") as f:
            f.write("UPDATE_ALLOW_UNSIGNED=true\n")
        self.assertEqual(self.apply(self.bundle(sign=False))["state"], "done")

    def test_signed_with_another_key(self):
        other = self.root / "other.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(other)], check=True)
        self.assert_refused(self.apply(self.bundle(key=other)), "openssl")

    def test_no_public_key_on_the_device(self):
        self.pubkey.unlink()
        self.assert_refused(self.apply(self.bundle()), "No public key")

    def test_an_old_style_bundle(self):
        path = self.watcher.INCOMING / "old.tar"
        with tarfile.open(path, "w") as tar:
            info = tarfile.TarInfo("images.tar")
            tar.addfile(info, io.BytesIO(b""))
        self.assert_refused(self.apply(path), "no manifest.json")

    def test_wrong_format(self):
        self.assert_refused(self.apply(self.bundle(manifest_extra={"format": 1})), "Unsupported bundle format 1")

    def test_a_manifest_that_isnt_one(self):
        real = self.watcher.read_json
        with mock.patch.object(self.watcher, "read_json",
                               side_effect=lambda path, default=None: ["no"] if path.name == "manifest.json" else real(path, default)):
            self.assert_refused(self.apply(self.bundle()), "Unsupported bundle format ?")

    def test_wrong_platform(self):
        self.assert_refused(self.apply(self.bundle(platform="linux/arm/v7")), "is for linux/arm/v7")

    def test_not_enough_space(self):
        with mock.patch.object(self.watcher.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            self.assert_refused(self.apply(self.bundle()), "Not enough space")

    def test_older_than_installed(self):
        self.watcher.write_json(self.watcher.INSTALLED_FILE, {"version": "v3", "built_at": time.time() + 1000})
        self.assert_refused(self.apply(self.bundle()), "older than what is installed")
        self.assertEqual(self.apply(self.bundle(), {"allow_downgrade": True})["state"], "done")

    def test_damaged_file(self):
        self.assert_refused(self.apply(self.bundle(tamper=True)), "images.tar in the bundle is damaged")

    def test_waits_while_a_restore_runs(self):
        self.watcher.write_json(self.watcher.MAINTENANCE_FILE, {"kind": "restore", "updated": time.time()})
        bundle = self.bundle()
        self.assertFalse(self.watcher.process_bundle(bundle))
        self.assertTrue(bundle.exists())  # tried again later
        self.watcher.write_json(self.watcher.MAINTENANCE_FILE, {"kind": "restore", "updated": 0})
        self.assertIsNone(self.watcher.other_maintenance())  # a leftover from a dead process

    # --- going back ---

    def test_a_new_version_that_doesnt_come_up_is_rolled_back(self):
        self.health = [False, True]  # the new version: no; the old one again: yes
        state = self.apply(self.bundle())
        self.assertEqual(state["state"], "rolled_back", state)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertEqual((self.project / "install" / "install.sh").read_text(), "#!/bin/sh\n")
        self.assertEqual(self.docker.restored, [b"PGDMP-dump"])
        self.assertIn(("docker", "tag", "coffeepy-web:coffeepy-previous-latest", "coffeepy-web:latest"), self.docker.calls)
        self.assertFalse(self.watcher.PREVIOUS_FILE.exists())
        self.assertIn(("update.apply", "failure"), self.events())

    def test_when_the_rollback_fails_too(self):
        self.health = [False, False]
        state = self.apply(self.bundle())
        self.assertEqual(state["state"], "failed")
        self.assertIn("so did the rollback", state["message"])
        self.assertIn(("update.rollback", "failure"), self.events())

    def test_a_failing_step_after_the_snapshot_rolls_back(self):
        self.docker.fail.add("load")
        self.assertEqual(self.apply(self.bundle())["state"], "rolled_back")

    def test_rollback_by_hand(self):
        self.apply(self.bundle())
        (self.watcher.INCOMING / "rollback.request").write_text(json.dumps({"requested_by": "boss"}))
        self.watcher.cycle()
        state = self.state()
        self.assertEqual((state["state"], state["kind"]), ("rolled_back", "rollback"))
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertEqual(self.events()[-1], ("update.rollback", "success"))
        (self.watcher.INCOMING / "rollback.request").write_text("{}")
        self.watcher.cycle()  # nothing to go back to any more
        self.assertEqual(self.state()["state"], "failed")
        self.assertEqual(self.events()[-1], ("update.rollback", "failure"))

    def test_rollback_without_a_saved_install_dir(self):
        self.apply(self.bundle())
        shutil.rmtree(self.watcher.UPDATES / "install.previous")
        (self.watcher.INCOMING / "rollback.request").write_text("{}")
        self.watcher.cycle()
        self.assertEqual(self.state()["state"], "rolled_back")

    # --- the rest ---

    def test_cycle_tidies_up_and_takes_bundles_in_order(self):
        stale = self.watcher.INCOMING / ".uploading-old"
        stale.write_bytes(b"x")
        os.utime(stale, (1, 1))
        fresh = self.watcher.INCOMING / ".uploading-new"
        fresh.write_bytes(b"x")
        with mock.patch.object(self.watcher, "process_bundle") as process:
            (self.watcher.INCOMING / "b.tar").write_bytes(b"")
            (self.watcher.INCOMING / "a.tar.gz").write_bytes(b"")
            self.watcher.cycle()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual([c.args[0].name for c in process.call_args_list], ["b.tar", "a.tar.gz"])

    def test_main(self):
        with mock.patch.object(self.watcher, "cycle", side_effect=[RuntimeError("boom"), None, KeyboardInterrupt]), \
                mock.patch.object(self.watcher.time, "sleep"):
            self.assertEqual(self.watcher.main(once=True), 0)
            with self.assertRaises(KeyboardInterrupt):
                self.watcher.main()

    def test_small_parts(self):
        (self.project / ".env").unlink()
        self.assertEqual(self.watcher.env_values(), {})
        self.assertEqual(self.watcher.add_env_defaults({}), [])
        self.assertEqual(self.watcher.read_json(self.root / "missing", "default"), "default")
        self.assertEqual(self.watcher.previous_tag("image"), "image:coffeepy-previous")
        with mock.patch("builtins.open", side_effect=OSError("full")):
            self.watcher.event("update.start")  # logged, not raised


class HealthCheckTestCase(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.watcher = load_watcher(self.root, self.root / "pub.pem")

    def test_healthy(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        with mock.patch.object(self.watcher.ssl, "create_default_context"), \
                mock.patch.object(self.watcher.urllib.request, "urlopen", side_effect=[OSError("refused"), response]), \
                mock.patch.object(self.watcher.time, "sleep"):
            self.assertTrue(self.watcher.healthy(timeout=60))

    def test_never_healthy(self):
        response = mock.MagicMock(status=502)
        response.__enter__.return_value = response
        with mock.patch.object(self.watcher.ssl, "create_default_context"), \
                mock.patch.object(self.watcher.urllib.request, "urlopen", return_value=response):
            self.assertFalse(self.watcher.healthy(timeout=0))
        with mock.patch.object(self.watcher.ssl, "create_default_context"), \
                mock.patch.object(self.watcher.urllib.request, "urlopen", side_effect=OSError), \
                mock.patch.object(self.watcher, "HEALTH_TIMEOUT", 0):
            self.assertFalse(self.watcher.healthy())

    def test_run_and_platform(self):
        self.assertEqual(self.watcher.run("echo", "hi").stdout, b"hi\n")
        self.assertEqual(self.watcher.run("false", check=False).returncode, 1)
        with self.assertRaises(self.watcher.UpdateError):
            self.watcher.run("sh", "-c", "echo bad >&2; exit 1")
        with mock.patch.object(self.watcher.platform, "machine", return_value="aarch64"):
            self.assertEqual(self.watcher.host_platform(), "linux/arm64")
        with mock.patch.object(self.watcher.platform, "machine", return_value="riscv64"):
            self.assertEqual(self.watcher.host_platform(), "riscv64")
        with mock.patch.object(self.watcher, "run") as run:
            self.watcher.compose("ps")
        run.assert_called_once_with("docker", "compose", "ps", cwd=self.watcher.PROJECT_DIR)
