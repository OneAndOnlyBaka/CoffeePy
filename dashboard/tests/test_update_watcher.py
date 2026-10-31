"""install/update_watcher.py against a simulated device: real bundles (tar, manifest, Ed25519
signature checked by the real openssl), docker and compose replaced by a model that records
what was done - so every check, the update, the automatic rollback and a power cut at any step run
without Docker."""
import gzip
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
SIGN_SCRIPT = settings.BASE_DIR / "scripts" / "sign_update_bundle.sh"
# What `pg_dump --format plain | gzip` gives - its end as PostgreSQL 16.10+ writes it.
DUMP = gzip.compress(b"COPY public.t (i) FROM stdin;\n1\n\\.\n\n--\n-- PostgreSQL database dump complete\n--\n\n"
                     b"\\unrestrict abc\n\n")


class PowerCut(BaseException):
    """Not an Exception: nothing in the watcher may catch it - a power cut runs no except: and
    no rollback (the finally: blocks it does run only tidy up the temporary directory)."""


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
        self.next_images = None  # what the new compose file names, once it is up
        self.tags = {}
        self.restored = []
        self.dump = DUMP
        self.fail = set()
        self.ps = (b'{"ID":"aaa111","Service":"web","Image":"coffeepy-web:latest","State":"running"}\n'
                   b'{"ID":"bbb222","Service":"db","Image":"postgres:16-alpine","State":"running"}\n')
        # The migrations the new image knows, and those the database has - the same, normally.
        self.known = [["coffee", "0001_initial"], ["coffee", "0002_more"], ["dashboard", "0001_initial"]]
        self.applied = [tuple(m) for m in self.known]

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
        if args[:5] == ("docker", "compose", "ps", "-q", "db"):
            stdout = b"bbb222\n"
        elif args[:3] == ("docker", "compose", "ps"):
            stdout = self.ps
        elif args[:2] == ("docker", "inspect") and "{{range" in args[3]:
            stdout = b"coffeepy_default \n"
        elif args[:2] == ("docker", "run") and "MigrationLoader" in args[-1]:
            leaves = {}
            for app, name in self.known:
                leaves[app] = max(leaves.get(app, ""), name)
            stdout = b"some warning\n" + json.dumps({"nodes": self.known, "leaves": sorted(leaves.items())}).encode()
        elif args[:2] == ("docker", "compose") and "django_migrations" in args[-1]:
            stdout = "".join(f"{app} {name}\n" for app, name in self.applied).encode()
        elif args[:2] == ("docker", "inspect"):
            stdout = b"aaa111ffffff sha256:0123456789abcdef\nbbb222ffffff sha256:fedcba9876543210\n"
        elif args[:2] == ("docker", "version") or args[:3] == ("docker", "compose", "version"):
            stdout = b"28.1.1"
        elif args[:2] == ("docker", "compose") and "config" in args:
            stdout = "\n".join(self.images).encode()
        elif args[:2] == ("docker", "compose") and args[2:5] == ("exec", "-T", "db") and "pg_dump" in args[-1]:
            stdout = self.dump
        elif args[:2] == ("docker", "compose") and args[2:5] == ("exec", "-T", "db") and "psql" in args[-1]:
            self.restored.append((args[-1], stdin))
        elif args[:2] == ("docker", "tag"):
            self.tags[args[3]] = args[2]
        elif args[:4] == ("docker", "compose", "up", "-d") and self.next_images:
            self.images = self.next_images
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
        self.assertEqual(len(list(self.watcher.PRE_UPDATE.glob("*.sql.gz"))), 1)
        self.assertIn(("docker", "load", "-i"), [c[:3] for c in self.docker.calls])
        self.assertIn(str(self.project / "install" / "install.sh"), [c[0] for c in self.docker.calls])
        self.assertIn(("systemctl", "restart", "--no-block", "coffeepy-update-watcher.service"), self.docker.calls)
        self.assertFalse(bundle.exists())
        self.assertFalse(self.watcher.MAINTENANCE_FILE.exists())
        self.assertEqual(self.events(), [("update.start", "success"), ("update.apply", "success")])
        # No way back once it came up: the old version's copies are gone, its images untagged for prune.
        self.assertFalse(self.watcher.PREVIOUS_FILE.exists())
        self.assertFalse((self.watcher.UPDATES / "install.previous").exists())
        self.assertFalse((self.watcher.UPDATES / "docker-compose.yml.previous").exists())
        removed = [c for c in self.docker.calls if c[:3] == ("docker", "image", "rm")]
        self.assertEqual([c[3] for c in removed], ["coffeepy-web:coffeepy-previous-latest",
                                                   "postgres:coffeepy-previous-16-alpine"])
        self.assertLess(self.docker.calls.index(removed[-1]), self.docker.calls.index(("docker", "image", "prune", "-f")))

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
        kept = [*self.watcher.PRE_UPDATE.glob("*.sql.gz"), *self.watcher.PRE_UPDATE.glob("*.dump")]
        self.assertEqual(len(kept), self.watcher.KEEP_PRE_UPDATE)
        self.assertIn("old3.dump", [p.name for p in kept])  # an older watcher's: kept by age, like the rest

    # --- refused before anything changes ---

    def assert_refused(self, state, text):
        self.assertEqual(state["state"], "failed", state)
        self.assertIn(text, state["message"])
        self.assertEqual(self.docker.calls, [])  # nothing touched
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")

    def test_unsigned(self):
        self.assert_refused(self.apply(self.bundle(sign=False)), "not signed")

    # scripts/sign_update_bundle.sh: the release workflow's unsigned bundle, signed at home

    def sign(self, bundle, *args):
        return subprocess.run(["bash", str(SIGN_SCRIPT), str(bundle), "--key", str(self.key), *args],
                              capture_output=True, text=True)

    def test_an_unsigned_bundle_signed_at_home(self):
        unsigned = self.bundle(name="coffeepy-update-v2-arm64-unsigned.tar", sign=False)
        result = self.sign(unsigned)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(hashlib.sha256(self.pubkey.read_bytes()).hexdigest()[:16], result.stdout)  # what the device shows
        signed = unsigned.with_name("coffeepy-update-v2-arm64.tar")
        unsigned.unlink()
        self.assertEqual(self.apply(signed)["state"], "done")

    def test_a_signature_of_another_key_is_replaced(self):
        other = self.root / "other.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(other)], check=True)
        theirs = self.bundle(name="theirs.tar", key=other)
        result = self.sign(theirs, "--output", str(self.watcher.INCOMING / "ours.tar"))
        self.assertEqual(result.returncode, 0, result.stderr)
        theirs.unlink()
        self.assertEqual(self.apply(self.watcher.INCOMING / "ours.tar")["state"], "done")

    def test_a_damaged_bundle_is_not_signed(self):
        unsigned = self.bundle(name="broken-unsigned.tar", sign=False, tamper=True)
        result = self.sign(unsigned)
        self.assertEqual(result.returncode, 1)
        self.assertIn("images.tar: checksum differs", result.stderr)
        self.assertFalse(unsigned.with_name("broken.tar").exists())
        self.assertFalse(unsigned.with_name("broken.tar.part").exists())

    def test_check_turned_off_on_this_device(self):
        with open(self.project / ".env", "a") as f:
            f.write("UPDATE_VERIFY_SIGNATURE=false\n")
        self.assertEqual(self.apply(self.bundle(sign=False))["state"], "done")
        other = self.root / "other.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(other)], check=True)
        self.assertEqual(self.apply(self.bundle(key=other))["state"], "done")  # any signature, or none

    def test_the_devices_own_key_wins(self):
        own = self.root / "own.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(own)], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(own), "-pubout", "-out", str(self.watcher.OWN_PUBLIC_KEY)],
                       check=True)
        self.assert_refused(self.apply(self.bundle()), "openssl")  # the bundled key no longer counts
        self.assertEqual(self.apply(self.bundle(key=own))["state"], "done")

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
        self.watcher.write_json(self.watcher.INSTALLED_FILE, {"version": "2.1.0", "built_at": time.time() + 1000})
        self.assert_refused(self.apply(self.bundle(version="2.0.2")), "updates only go forward")
        # What an older dashboard still sends changes nothing: there is no going back by hand.
        self.assert_refused(self.apply(self.bundle(version="2.0.2"), {"allow_downgrade": True}), "only go forward")

    def test_a_pre_release_only_goes_back_to_a_release_of_its_major_version(self):
        later = time.time() + 1000
        for running, bundle in (("3.0.0-beta.1", "2.0.2"), ("2.1.0-feature-x.5", "2.1.0-feature-y.2"),
                                ("2.1.0-feature-x.5", "nightly"), ("dev", "2.0.2")):
            with self.subTest(running=running, bundle=bundle):
                self.watcher.write_json(self.watcher.INSTALLED_FILE, {"version": running, "built_at": later})
                self.assert_refused(self.apply(self.bundle(version=bundle)), "only go forward")

    def test_a_pre_release_goes_back_to_its_release(self):
        self.watcher.write_json(self.watcher.INSTALLED_FILE, {"version": "v2.0.2-7-g303aaeb+dirty",
                                                              "built_at": time.time() + 1000})
        self.docker.applied += [("coffee", "0003_pre_release"), ("prerelease_app", "0001_initial")]
        state = self.apply(self.bundle(version="2.0.2"))
        self.assertEqual(state["state"], "done", state)
        self.assertFalse(self.watcher.ACTIVE.exists())  # the release's watcher wouldn't clear it
        migrate = [c for c in self.docker.calls if "manage.py" in c]
        previous = "coffeepy-web:coffeepy-previous-latest"
        env = ("docker", "run", "--rm", "--env-file", str(self.project / ".env"), "-e", "COFFEEPY_PREPARE=0",
               "--network", "coffeepy_default", previous, "python", "manage.py", "migrate")
        # By the version that knows these migrations - the running one - after the dump, before the start.
        self.assertEqual(migrate, [(*env, "coffee", "0002_more", "--no-input"),
                                   (*env, "prerelease_app", "zero", "--no-input")])
        dump = next(i for i, c in enumerate(self.docker.calls) if "pg_dump" in c[-1])
        up = self.docker.calls.index(("docker", "compose", "up", "-d", "--remove-orphans"))
        self.assertLess(dump, self.docker.calls.index(migrate[0]))
        self.assertLess(self.docker.calls.index(migrate[-1]), up)
        log = (self.watcher.STATUS_DIR / "coffeepy-update-v2.tar.log").read_text()
        self.assertIn("Database migrations taken back: coffee to 0002_more, prerelease_app to zero", log)

    def test_a_migration_that_cant_be_taken_back_rolls_the_update_back(self):
        self.docker.applied.append(("coffee", "0003_pre_release"))
        self.docker.fail.add("manage.py migrate")
        state = self.apply(self.bundle())
        self.assertEqual(state["state"], "rolled_back", state)
        self.assertEqual(len(self.docker.restored), 1)  # the dump from before, back in
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")

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
        self.assertEqual([stdin for _, stdin in self.docker.restored], [DUMP])
        self.assertIn("gunzip -c && echo 'COMMIT;'", self.docker.restored[0][0])
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

    def test_rolled_back_on_a_device_without_install_dir(self):
        shutil.rmtree(self.project / "install")
        self.health = [False, True]
        self.assertEqual(self.apply(self.bundle(install=False))["state"], "rolled_back")
        self.assertFalse((self.project / "install").exists())

    # --- the database dump ---

    def test_an_incomplete_dump_stops_the_update_before_the_switch(self):
        for dump, text in ((gzip.compress(b"COPY public.t (i) FROM stdin;\n1\n"), "is incomplete"),
                           (b"not gzip at all", "is damaged")):
            with self.subTest(text):
                self.docker.dump = dump
                state = self.apply(self.bundle())
                self.assertEqual(state["state"], "rolled_back", state)
                self.assertIn(text, state["message"])
                self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
                self.assertEqual(self.docker.restored, [])  # the database was never touched
                self.assertIn(("docker", "compose", "up", "-d", "--remove-orphans"), self.docker.calls)  # writers back
                self.assertFalse(self.watcher.PREVIOUS_FILE.exists())  # nothing to go back to by hand
                self.assertFalse(self.watcher.JOURNAL_FILE.exists())

    def test_when_the_running_version_doesnt_come_back_after_an_incomplete_dump(self):
        self.docker.dump = gzip.compress(b"partial")
        self.health = [False]
        state = self.apply(self.bundle())
        self.assertEqual(state["state"], "failed")
        self.assertIn("does not come up either", state["message"])
        self.assertTrue(self.watcher.JOURNAL_FILE.exists())  # for the next start

    def test_writers_are_stopped_before_the_dump(self):
        self.apply(self.bundle())
        calls = [" ".join(c) for c in self.docker.calls]
        stop = calls.index("docker compose stop web mqtt-listener backup")
        dump = next(k for k, c in enumerate(calls) if "pg_dump" in c)
        load = next(k for k, c in enumerate(calls) if c.startswith("docker load"))
        self.assertLess(load, stop)  # the app keeps serving while the images load
        self.assertLess(stop, dump)

    def test_a_dump_changed_since_it_was_saved_is_not_loaded(self):
        def new_version_fails():
            next(self.watcher.PRE_UPDATE.glob("*.sql.gz")).write_bytes(DUMP + b"x")
            return False

        self.patch(self.watcher, "healthy", lambda timeout=None: new_version_fails())
        state = self.apply(self.bundle())
        self.assertEqual(state["state"], "failed")
        self.assertIn("checksum", state["message"])
        self.assertEqual(self.docker.restored, [])

    def test_an_image_the_new_version_no_longer_names_is_untagged(self):
        self.docker.images = ["coffeepy-web:latest", "postgres:16.15-alpine"]
        self.docker.next_images = ["coffeepy-web:latest", "postgres:16.16-alpine"]
        state = self.apply(self.bundle())
        self.assertEqual(state["state"], "done", state)
        removed = [c[3] for c in self.docker.calls if c[:3] == ("docker", "image", "rm")]
        self.assertEqual(removed, ["coffeepy-web:coffeepy-previous-latest",
                                   "postgres:coffeepy-previous-16.15-alpine", "postgres:16.15-alpine"])

    # --- OS bundles ---

    def os_bundle(self, release="trixie", built_at=None, script=True, name="coffeepy-os-trixie-1-arm64.tar"):
        work = Path(tempfile.mkdtemp(dir=self.root, prefix="build-"))
        (work / "Packages").write_text("")
        with tarfile.open(work / "os.tar", "w") as tar:
            tar.add(work / "Packages", "debs/Packages")
        (work / "os-update.sh").write_text("#!/bin/bash\n")
        members = ["os.tar", "os-update.sh"] if script else ["os.tar"]
        manifest = {"format": 2, "kind": "os", "version": "os-trixie-1", "built_at": built_at or time.time(),
                    "platform": "linux/arm64", "release": release, "required_mb": 1,
                    "files": {n: hashlib.sha256((work / n).read_bytes()).hexdigest() for n in members}}
        (work / "manifest.json").write_text(json.dumps(manifest))
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(self.key), "-rawin",
                        "-in", str(work / "manifest.json"), "-out", str(work / "manifest.sig")], check=True)
        path = self.watcher.INCOMING / name
        with tarfile.open(path, "w") as tar:
            for member in ["manifest.json", "manifest.sig", *members]:
                tar.add(work / member, member)
        return path

    def os_device(self, release="trixie"):
        self.patch(self.watcher, "_os_field", lambda name: release if name == "VERSION_CODENAME" else "")
        self.reboot = self.root / "reboot-required"
        mock.patch.object(self.watcher, "REBOOT_REQUIRED", self.reboot).start()
        self.addCleanup(mock.patch.stopall)

    def ran_script(self):
        return [c for c in self.docker.calls if c[0] == "bash" and c[1].endswith("os-update.sh")]

    def test_an_os_update(self):
        self.os_device()
        bundle = self.os_bundle()
        original = self.docker.run

        def run(*args, **kwargs):
            if args[0] == "bash":
                original(*args, **kwargs)
                return SimpleNamespace(returncode=0, stdout=b"Brought up to date: 3 package(s).\nKept back, ...: libx\n",
                                       stderr=b"")
            return original(*args, **kwargs)

        self.watcher.run.side_effect = run
        state = self.apply(bundle, {"requested_by": "boss"})
        self.assertEqual(state["state"], "done", state)
        self.assertIn("Brought up to date: 3 package(s).", (self.watcher.STATUS_DIR / f"{bundle.name}.log").read_text())
        self.assertIn("Some packages were kept back", state["message"])
        [script] = self.ran_script()
        self.assertTrue(script[2].endswith("/os"))
        self.assertEqual(len(list(self.watcher.PRE_UPDATE.glob("before-os-trixie-1_*.sql.gz"))), 1)
        self.assertEqual(json.loads(self.watcher.OS_INSTALLED_FILE.read_text())["version"], "os-trixie-1")
        self.assertFalse(self.watcher.INSTALLED_FILE.exists())  # the app's version is not the OS bundle's
        self.assertNotIn(("docker", "load", "-i"), [c[:3] for c in self.docker.calls])
        self.assertFalse(self.watcher.OS_PENDING_FILE.exists())
        self.assertFalse(bundle.exists())
        self.assertFalse(self.watcher.MAINTENANCE_FILE.exists())
        self.assertEqual(self.events(), [("update.start", "success"), ("os_update.apply", "success")])

    def test_an_os_update_that_asks_for_a_restart(self):
        self.os_device()
        self.reboot.write_text("*** System restart required ***\n")
        state = self.apply(self.os_bundle())
        self.assertEqual((state["state"], state["step"]), ("running", "restart"))
        self.assertIn(("systemctl", "reboot"), self.docker.calls)
        self.assertEqual(json.loads(self.watcher.OS_PENDING_FILE.read_text())["phase"], "restart")
        self.assertTrue(self.watcher.MAINTENANCE_FILE.exists())  # across the restart
        self.assertEqual(list(self.watcher.INCOMING.glob("*.tar")), [])  # or it would restart again and again
        # The early recovery at boot leaves it to the watcher, which waits for the stack.
        self.assertTrue(self.watcher.recover_os(boot=True))
        self.assertTrue(self.watcher.OS_PENDING_FILE.exists())
        self.health = [False]
        self.watcher.main(once=True)
        self.assertEqual(self.state()["state"], "failed")
        self.assertIn("did not answer", self.state()["message"])
        self.assertFalse(self.watcher.OS_PENDING_FILE.exists())
        self.assertFalse(self.watcher.MAINTENANCE_FILE.exists())
        self.assertEqual(self.events()[-1], ("os_update.restart", "failure"))

    def test_an_os_update_whose_script_fails(self):
        self.os_device()
        self.docker.fail.add("os-update.sh")
        state = self.apply(self.os_bundle())
        self.assertEqual(state["state"], "failed")
        self.assertIn("operating system update failed", state["message"])
        self.assertFalse(self.watcher.OS_INSTALLED_FILE.exists())
        self.assertFalse(self.watcher.OS_PENDING_FILE.exists())
        self.assertEqual(self.events()[-1], ("os_update.apply", "failure"))

    def test_an_os_bundle_is_refused_before_anything_changes(self):
        self.os_device(release="bookworm")
        state = self.apply(self.os_bundle())
        self.assertIn("is for trixie, this device runs bookworm", state["message"])
        self.os_device(release="")
        self.assertIn("an unknown release", self.apply(self.os_bundle())["message"])
        self.os_device()
        self.assertIn("lacks its packages or its script", self.apply(self.os_bundle(script=False))["message"])
        self.watcher.write_json(self.watcher.OS_INSTALLED_FILE, {"built_at": time.time() + 60})
        self.assertIn("only go forward", self.apply(self.os_bundle())["message"])
        self.docker.fail.add("pg_dump")
        self.watcher.OS_INSTALLED_FILE.unlink()
        self.assertIn("refused", self.apply(self.os_bundle())["message"])
        self.assertEqual(self.ran_script(), [])
        self.assertFalse(self.watcher.OS_PENDING_FILE.exists())

    def test_an_os_update_a_power_cut_interrupted(self):
        self.os_device()
        bundle = self.os_bundle()
        original = self.docker.run

        def run(*args, **kwargs):
            if args[0] == "bash":
                raise PowerCut("in the middle of apt")
            return original(*args, **kwargs)

        self.watcher.run.side_effect = run
        with self.assertRaises(PowerCut):
            self.apply(bundle)
        self.assertTrue(bundle.exists())
        self.assertEqual(json.loads(self.watcher.OS_PENDING_FILE.read_text())["phase"], "packages")
        self.watcher.run.side_effect = original
        self.assertEqual(self.watcher.main(recover_only=True), 0)  # at boot, before the stack
        self.assertIn(("dpkg", "--configure", "-a"), self.docker.calls)
        self.assertFalse(bundle.exists())  # not taken up again by itself
        self.assertFalse(self.watcher.OS_PENDING_FILE.exists())
        self.assertIn("dpkg finished what it had begun", self.state()["message"])
        self.assertEqual(self.events()[-1], ("os_update.recover", "failure"))
        self.assertFalse(self.watcher.recover_os(boot=True))  # nothing left

    def test_an_interrupted_os_update_dpkg_cant_finish(self):
        self.os_device()
        self.watcher.write_json(self.watcher.OS_PENDING_FILE, {"bundle": "b.tar", "version": "v", "phase": "packages"})
        self.docker.fail.add("dpkg")
        self.assertTrue(self.watcher.recover_os(boot=False))
        self.assertIn("dpkg could not finish it", self.state()["message"])

    def test_the_boot_recovery_runs_for_os_updates_too(self):
        # Measured on the test Pi: with only the journal as its condition, a power cut in dpkg was
        # finished by the watcher - after the stack had started on the half-done packages.
        unit = (settings.BASE_DIR / "install" / "coffeepy-update-recover.service").read_text()
        for path in (self.watcher.JOURNAL_FILE, self.watcher.OS_PENDING_FILE):
            self.assertIn(f"ConditionPathExists=|/opt/coffeepy/data/updates/{path.name}", unit)

    def test_the_installed_packages_for_an_os_bundle(self):
        original = self.docker.run

        def run(*args, **kwargs):
            if args[0] == "dpkg-query":
                return SimpleNamespace(returncode=0, stdout=b"ii  zlib1g\nrc  gone\nii  bash\n", stderr=b"")
            return original(*args, **kwargs)

        self.watcher.run.side_effect = run
        self.watcher.write_json(self.watcher.OS_INSTALLED_FILE, {"version": "os-trixie-1"})
        self.assertEqual(self.system()["os_bundle"], {"version": "os-trixie-1"})
        self.assertEqual(self.watcher.OS_PACKAGES_FILE.read_text(), "bash\nzlib1g\n")

    def test_an_older_watchers_rollback_copies_go_at_the_first_start(self):
        (self.watcher.PRE_UPDATE / "before-v1.dump").write_bytes(b"PGDMP")
        self.watcher.write_json(self.watcher.PREVIOUS_FILE, {"images": ["coffeepy-web:latest"],
                                                             "database": "before-v1.dump", "installed": {}})
        (self.watcher.UPDATES / "docker-compose.yml.previous").write_text("services: {v1: {}}\n")
        (self.watcher.UPDATES / "install.previous").mkdir()
        (self.watcher.INCOMING / "rollback.request").write_text("{}")  # asked for in the old dashboard
        self.watcher.main(once=True)
        self.assertIn(("docker", "image", "rm", "coffeepy-web:coffeepy-previous-latest"), self.docker.calls)
        for gone in (self.watcher.PREVIOUS_FILE, self.watcher.UPDATES / "docker-compose.yml.previous",
                     self.watcher.UPDATES / "install.previous", self.watcher.INCOMING / "rollback.request"):
            self.assertFalse(gone.exists(), gone)
        self.assertTrue((self.watcher.PRE_UPDATE / "before-v1.dump").exists())  # a copy of the data: stays
        self.assertEqual(self.docker.restored, [])

    # --- power cuts: what the next boot finds ---

    def cut_power_at(self, phase):
        """The update stops dead right after the journal recorded `phase` - nothing of it runs
        any more, not even its error handling."""
        real = self.watcher.journal

        def journal(name, *args, **kwargs):
            real(name, *args, **kwargs)
            if name == phase:
                raise PowerCut(phase)

        patcher = mock.patch.object(self.watcher, "journal", side_effect=journal)
        patcher.start()
        return patcher

    def interrupted(self, phase, **bundle_args):
        bundle = self.bundle(**bundle_args)
        patcher = self.cut_power_at(phase)
        try:
            with self.assertRaises(PowerCut):
                self.apply(bundle, {"requested_by": "boss", "note": "kept"})
        finally:
            patcher.stop()
        return bundle

    def test_a_power_cut_at_any_step_is_undone_and_the_update_runs_again(self):
        old_compose = "services: {old: {}}\n"
        for phase in self.watcher.PHASES:
            with self.subTest(phase):
                (self.project / "docker-compose.yml").write_text(old_compose)
                shutil.rmtree(self.project / "install")
                (self.project / "install").mkdir()
                (self.project / "install" / "install.sh").write_text("#!/bin/sh\n")
                self.docker.calls.clear()
                self.docker.restored.clear()
                bundle = self.interrupted(phase)
                self.assertTrue(self.watcher.JOURNAL_FILE.exists())
                self.assertTrue((self.watcher.ACTIVE / bundle.name).exists() or bundle.exists())

                self.assertTrue(self.watcher.recover())
                self.assertEqual((self.project / "docker-compose.yml").read_text(), old_compose)
                self.assertEqual((self.project / "install" / "install.sh").read_text(), "#!/bin/sh\n")
                self.assertFalse(self.watcher.JOURNAL_FILE.exists())
                self.assertFalse(self.watcher.ACTIVE.exists())
                self.assertFalse(self.watcher.PREVIOUS_FILE.exists())
                self.assertEqual(self.state()["state"], "rolled_back")
                self.assertIn("attempt 2 of 3", self.state()["message"])
                if phase != "accepted":
                    self.assertIn(("docker", "tag", "coffeepy-web:coffeepy-previous-latest", "coffeepy-web:latest"),
                                  self.docker.calls)
                self.assertEqual(len(self.docker.restored), 1 if phase == "switched" else 0)
                options = json.loads(bundle.with_name(bundle.name + ".options.json").read_text())
                self.assertEqual(options, {"requested_by": "boss", "note": "kept", "attempt": 2})

                self.watcher.cycle()  # and again, from the start
                self.assertEqual(self.state()["state"], "done", phase)
                self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {new: {}}\n")
                self.assertFalse(bundle.exists())
                self.assertFalse(self.watcher.JOURNAL_FILE.exists())
                log = (self.watcher.STATUS_DIR / f"{bundle.name}.log").read_text()
                self.assertIn("The update was interrupted", log)  # one log over all attempts
                self.assertIn("attempt 2 of 3", log)
        self.assertIn(("update.recover", "success"), self.events())

    def test_a_power_cut_while_the_new_version_starts(self):
        bundle = self.bundle()
        self.patch(self.watcher, "healthy", mock.Mock(side_effect=[PowerCut("verify"), True]))
        with self.assertRaises(PowerCut):
            self.apply(bundle)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {new: {}}\n")
        self.watcher.recover()
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertEqual([stdin for _, stdin in self.docker.restored], [DUMP])  # the new version may have migrated
        self.assertTrue((self.watcher.INCOMING / bundle.name).exists())

    def test_a_power_cut_in_the_last_attempt_is_not_tried_again(self):
        bundle = self.bundle()
        bundle.with_name(bundle.name + ".options.json").write_text(json.dumps({"attempt": 3}))
        patcher = self.cut_power_at("loaded")
        with self.assertRaises(PowerCut):
            self.watcher.process_bundle(bundle)
        patcher.stop()
        self.watcher.recover()
        self.assertIn("stays on the version before it", self.state()["message"])
        self.assertFalse(bundle.exists())
        self.assertFalse(self.watcher.ACTIVE.exists())
        self.assertEqual(self.events()[-1], ("update.recover", "failure"))

    def test_cut_before_the_bundle_moved_and_out_of_attempts(self):
        bundle = self.bundle()
        bundle.with_name(bundle.name + ".options.json").write_text(json.dumps({"attempt": 3}))
        patcher = self.cut_power_at("accepted")
        with self.assertRaises(PowerCut):
            self.watcher.process_bundle(bundle)
        patcher.stop()
        self.assertTrue(bundle.exists())  # still in incoming/
        self.watcher.recover()
        self.assertFalse(bundle.exists())  # or it would start over as a first attempt
        self.assertFalse(bundle.with_name(bundle.name + ".options.json").exists())

    def test_an_update_whose_rollback_failed_waits_for_the_next_start(self):
        self.health = [False, False]  # the new version doesn't come up, nor does the old one
        bundle = self.bundle()
        self.assertEqual(self.apply(bundle)["state"], "failed")
        self.assertTrue(self.watcher.JOURNAL_FILE.exists())
        self.assertTrue((self.watcher.ACTIVE / bundle.name).exists())
        other = self.bundle(name="other.tar")
        self.watcher.cycle()
        self.assertTrue(other.exists())  # nothing new before the device is back
        self.health = [False]
        self.watcher.recover()  # the next start: still no luck
        self.assertEqual(self.state()["state"], "failed")
        self.assertIn("tried again when the device restarts", self.state()["message"])
        self.assertEqual(self.events()[-1], ("update.recover", "failure"))
        self.watcher.recover()  # and the one after
        self.assertEqual(self.state()["state"], "rolled_back")
        self.assertFalse(self.watcher.JOURNAL_FILE.exists())
        self.assertFalse((self.watcher.INCOMING / bundle.name).exists())  # it failed on its own: not again

    def test_a_rollback_that_finished_before_the_journal_learned_of_it(self):
        self.watcher.write_json(self.watcher.JOURNAL_FILE, {"bundle": "b.tar", "phase": "switched", "retry": True,
                                                            "images": ["coffeepy-web:latest"]})
        self.assertTrue(self.watcher.recover())
        self.assertEqual(self.state()["state"], "rolled_back")
        self.assertEqual(self.docker.restored, [])  # previous.json gone: the database is back already
        self.assertIn("stays on the version before it", self.state()["message"])  # no bundle to run again

    def test_recover_without_a_journal_tidies_up(self):
        (self.watcher.UPDATES / ".work-old").mkdir()
        (self.watcher.ACTIVE).mkdir()
        (self.watcher.ACTIVE / "left.tar").write_bytes(b"x")
        self.assertFalse(self.watcher.recover())
        self.assertFalse((self.watcher.UPDATES / ".work-old").exists())
        self.assertFalse(self.watcher.ACTIVE.exists())
        self.assertEqual(self.docker.calls, [])

    def test_main_recovers_first(self):
        with mock.patch.object(self.watcher, "recover") as recover, mock.patch.object(self.watcher, "cycle") as cycle:
            self.assertEqual(self.watcher.main(recover_only=True), 0)
        recover.assert_called_once()
        cycle.assert_not_called()
        with mock.patch.object(self.watcher, "recover", side_effect=RuntimeError("boom")), \
                mock.patch.object(self.watcher, "cycle") as cycle:
            self.assertEqual(self.watcher.main(once=True), 0)
        cycle.assert_called_once()

    # --- files a power cut must not leave half-written ---

    def test_replacing_install_dir(self):
        target, new = self.root / "target", self.root / "new"
        for directory, text in ((target, "old"), (new, "new")):
            directory.mkdir()
            (directory / "f").write_text(text)
        with mock.patch.object(self.watcher, "_exchange", side_effect=OSError("EINVAL")):
            self.watcher.replace_dir(new, target)  # the fallback
        self.assertEqual((target / "f").read_text(), "new")
        self.assertFalse(new.exists())
        new.mkdir()
        (new / "f").write_text("newer")
        self.watcher.replace_dir(new, target)
        self.assertEqual((target / "f").read_text(), "newer")
        self.assertFalse(new.exists())
        shutil.rmtree(target)
        new.mkdir()
        self.watcher.replace_dir(new, target)  # a device that had no install/ yet
        self.assertTrue(target.is_dir())
        with self.assertRaises(OSError):
            self.watcher._exchange(self.root / "missing", target)
        with mock.patch.object(self.watcher.ctypes, "CDLL", return_value=object()), self.assertRaises(OSError):
            self.watcher._exchange(new, target)

    def test_env_defaults_on_a_device_without_env(self):
        (self.project / ".env").unlink()
        self.assertEqual(self.watcher.add_env_defaults({"A": "1"}), ["A"])
        self.assertEqual(os.stat(self.project / ".env").st_mode & 0o777, 0o600)
        (self.project / ".env").chmod(0o640)
        self.watcher.add_env_defaults({"B": "2"})
        self.assertEqual(os.stat(self.project / ".env").st_mode & 0o777, 0o640)  # kept
        self.assertIn("A=1\n", (self.project / ".env").read_text())

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

    # --- system.json, for the dashboard's version list ---

    def system(self):
        self.watcher.write_system_info()
        return json.loads(self.watcher.SYSTEM_FILE.read_text())

    def test_system_info(self):
        info = self.system()
        self.assertEqual(info["containers"], [
            {"service": "db", "image": "postgres:16-alpine", "image_id": "fedcba987654", "state": "running"},
            {"service": "web", "image": "coffeepy-web:latest", "image_id": "0123456789ab", "state": "running"},
        ])
        self.assertEqual((info["docker"], info["compose"]), ("28.1.1", "28.1.1"))
        self.assertEqual(info["signature"]["key"], "bundled")
        self.assertEqual(len(info["signature"]["fingerprint"]), 16)

    def test_system_info_with_older_compose_and_without_containers(self):
        self.docker.ps = b'[{"ID":"aaa111","Service":"web","Image":"coffeepy-web:latest"}]'
        self.assertEqual(self.system()["containers"][0]["state"], "")
        self.docker.ps = b"no json"
        self.assertEqual(self.system()["containers"], [])
        self.docker.ps = b"[]"
        self.docker.calls.clear()
        self.assertEqual(self.system()["containers"], [])
        self.assertNotIn(("docker", "inspect"), [c[:2] for c in self.docker.calls])

    def test_system_info_when_docker_is_missing_or_fails(self):
        self.docker.fail.add("version")
        self.assertEqual(self.system()["docker"], "")
        with mock.patch.object(self.watcher, "run", side_effect=OSError("no docker")):
            info = self.system()
        self.assertEqual((info["docker"], info["containers"]), ("", []))

    def test_system_info_reads_the_host(self):
        files = {"/proc/device-tree/model": "Raspberry Pi 3 Model B Rev 1.2\x00",
                 "/etc/os-release": 'NAME="Debian"\nPRETTY_NAME="Raspbian GNU/Linux 13 (trixie)"\n'}

        class FakePath(type(self.root)):
            def read_text(self, *args, **kwargs):
                if str(self) in files:
                    return files[str(self)]
                raise OSError(str(self))

        with mock.patch.object(self.watcher, "Path", FakePath):
            info = self.system()
        self.assertEqual((info["model"], info["os"]), ("Raspberry Pi 3 Model B Rev 1.2", "Raspbian GNU/Linux 13 (trixie)"))
        files["/etc/os-release"] = 'NAME="Debian"\n'
        files.pop("/proc/device-tree/model")
        with mock.patch.object(self.watcher, "Path", FakePath):
            info = self.system()
        self.assertEqual((info["model"], info["os"]), ("", ""))
        files.clear()
        with mock.patch.object(self.watcher, "Path", FakePath):
            self.assertEqual(self.system()["os"], "")

    def test_system_info_signature(self):
        self.watcher.OWN_PUBLIC_KEY.write_text(self.pubkey.read_text())
        self.assertEqual(self.system()["signature"]["key"], "own")
        self.watcher.OWN_PUBLIC_KEY.unlink()
        self.pubkey.unlink()
        self.assertEqual(self.system()["signature"], {"check": True, "key": "missing"})
        with open(self.project / ".env", "a") as f:
            f.write("UPDATE_VERIFY_SIGNATURE=false\n")
        self.assertEqual(self.system()["signature"], {"check": False})

    def test_cycle_refreshes_system_info_when_due_and_after_a_change(self):
        last = self.watcher.cycle()
        self.assertTrue(self.watcher.SYSTEM_FILE.exists())
        self.watcher.SYSTEM_FILE.unlink()
        self.assertEqual(self.watcher.cycle(last), last)  # not due yet
        self.assertFalse(self.watcher.SYSTEM_FILE.exists())
        with mock.patch.object(self.watcher, "process_bundle"):
            (self.watcher.INCOMING / "b.tar").write_bytes(b"")
            self.assertGreaterEqual(self.watcher.cycle(last), last)
        self.assertTrue(self.watcher.SYSTEM_FILE.exists())

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
