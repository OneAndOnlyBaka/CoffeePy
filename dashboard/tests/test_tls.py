import shutil
import subprocess
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from dashboard import tls

HOSTNAMES = ["192.168.4.1", "coffeepy.local"]


def make_cert(directory, name, hostname, days=30):
    """A throwaway self-signed certificate + key, standing in for an admin's own upload."""
    crt, key = Path(directory, f"{name}.crt"), Path(directory, f"{name}.key")
    subprocess.run(
        ["openssl", "req", "-x509", "-nodes", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-keyout", str(key), "-out", str(crt), "-days", str(days), "-subj", f"/CN={hostname}",
         "-addext", f"subjectAltName=DNS:{hostname}"],
        check=True, capture_output=True,
    )
    return crt.read_bytes(), key.read_bytes()


class TLSDirTestCase(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        override = override_settings(TLS_DIR=self.dir, TLS_HOSTNAMES=HOSTNAMES)
        override.enable()
        self.addCleanup(override.disable)

    def path(self, name):
        return Path(self.dir, name)


class EnsureTestCase(TLSDirTestCase):
    def test_first_run_creates_ca_certificates_and_caddyfile(self):
        done = tls.ensure()
        self.assertIn("created the device CA", done)
        for name in ("ca.crt", "ca.key", "server.crt", "server.key", "kiosk.crt", "kiosk.key", "Caddyfile"):
            self.assertTrue(self.path(name).exists(), name)
        self.assertEqual(tls.cert_sans(self.path("server.crt")), {"IP:192.168.4.1", "DNS:coffeepy.local"})
        self.assertEqual(tls.cert_sans(self.path("kiosk.crt")), {"DNS:localhost", "IP:127.0.0.1"})
        subprocess.run(["openssl", "verify", "-CAfile", str(self.path("ca.crt")), str(self.path("server.crt"))],
                       check=True, capture_output=True)
        self.assertEqual(self.path("ca.key").stat().st_mode & 0o777, 0o600)

    def test_second_run_changes_nothing(self):
        tls.ensure()
        ca = self.path("ca.crt").read_bytes()
        self.assertEqual(tls.ensure(), [])
        self.assertEqual(self.path("ca.crt").read_bytes(), ca)

    def test_new_hostname_reissues_the_server_certificate_but_keeps_the_ca(self):
        # Phones trust the CA - changing PUBLIC_HOST must not make them install it again.
        tls.ensure()
        ca = self.path("ca.crt").read_bytes()
        with override_settings(TLS_HOSTNAMES=HOSTNAMES + ["10.0.0.5"]):
            done = tls.ensure()
        self.assertTrue(any("network certificate" in d for d in done))
        self.assertIn("IP:10.0.0.5", tls.cert_sans(self.path("server.crt")))
        self.assertEqual(self.path("ca.crt").read_bytes(), ca)

    def test_caddyfile_serves_the_kiosk_only_on_the_loopback_port(self):
        tls.ensure()
        caddyfile = self.path("Caddyfile").read_text()
        network, kiosk = caddyfile.split(":443 {")[1].split(":8443 {")
        self.assertIn("respond @kiosk", network)
        self.assertNotIn("@kiosk", kiosk)
        self.assertIn("auto_https off", caddyfile)


class CustomCertificateTestCase(TLSDirTestCase):
    def setUp(self):
        super().setUp()
        tls.ensure()

    def test_upload_switches_port_443_and_leaves_the_kiosk_alone(self):
        kiosk_before = self.path("Caddyfile").read_text().split(":8443 {")[1]
        cert, key = make_cert(self.dir, "own", "coffeepy.local")
        warnings = tls.install_custom(cert, key)
        self.assertEqual(tls.active_certificate()["source"], "custom")
        self.assertTrue(any("192.168.4.1" in w for w in warnings))  # not in the uploaded SANs
        caddyfile = self.path("Caddyfile").read_text()
        live = caddyfile.split(":443 {")[1].split("tls /tls/")[1].split(".crt")[0]
        self.assertEqual(self.path(f"{live}.crt").read_bytes(), cert)
        self.assertEqual(caddyfile.split(":8443 {")[1], kiosk_before)

    def test_new_certificate_means_a_new_path_for_caddy(self):
        # Caddy only reloads when its parsed config changes - see the tls module docstring.
        before = self.path("Caddyfile").read_text()
        tls.install_custom(*make_cert(self.dir, "own", "coffeepy.local"))
        self.assertNotEqual(self.path("Caddyfile").read_text(), before)

    def test_key_of_another_certificate_is_rejected(self):
        cert, _ = make_cert(self.dir, "one", "coffeepy.local")
        _, other_key = make_cert(self.dir, "two", "coffeepy.local")
        with self.assertRaisesMessage(tls.TLSError, "does not belong"):
            tls.install_custom(cert, other_key)
        self.assertFalse(tls.has_custom())

    def test_garbage_is_rejected(self):
        with self.assertRaises(tls.TLSError):
            tls.install_custom(b"not a certificate", b"not a key")

    def test_revert_goes_back_to_the_device_certificate(self):
        tls.install_custom(*make_cert(self.dir, "own", "coffeepy.local"))
        tls.remove_custom()
        self.assertEqual(tls.active_certificate()["source"], "self-signed")
        self.assertEqual(tls.download()[1], "coffeepy-ca.crt")


class CertificateViewsTestCase(TLSDirTestCase):
    def setUp(self):
        super().setUp()
        tls.ensure()
        self.client = Client()

    def test_download_needs_no_login(self):
        response = self.client.get(reverse("certificate_download"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.path("ca.crt").read_bytes())
        self.assertIn("coffeepy-ca.crt", response["Content-Disposition"])

    def test_login_pages_link_the_download(self):
        self.assertContains(self.client.get(reverse("dashboard:login")), reverse("certificate_download"))

    def test_certificate_page_is_for_superusers_only(self):
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.assertNotEqual(self.client.get(reverse("dashboard:certificate")).status_code, 200)

    def test_superuser_uploads_and_reverts(self):
        User.objects.create_superuser(username="root", password="pw")
        self.client.login(username="root", password="pw")
        cert, key = make_cert(self.dir, "own", "coffeepy.local")
        response = self.client.post(reverse("dashboard:certificate"), {
            "certificate": SimpleUploadedFile("own.crt", cert), "key": SimpleUploadedFile("own.key", key),
        })
        self.assertContains(response, "Certificate installed")
        self.assertTrue(tls.has_custom())
        self.client.post(reverse("dashboard:certificate"), {"action": "revert"})
        self.assertFalse(tls.has_custom())

    def test_mismatched_upload_shows_the_reason(self):
        User.objects.create_superuser(username="root", password="pw")
        self.client.login(username="root", password="pw")
        cert, _ = make_cert(self.dir, "one", "coffeepy.local")
        _, key = make_cert(self.dir, "two", "coffeepy.local")
        response = self.client.post(reverse("dashboard:certificate"), {
            "certificate": SimpleUploadedFile("one.crt", cert), "key": SimpleUploadedFile("two.key", key),
        })
        self.assertContains(response, "does not belong")
        self.assertFalse(tls.has_custom())


class MaintenanceTestCase(TLSDirTestCase):
    """The "back in a moment" route and page Caddy answers with while the app is down."""

    def test_both_ports_serve_the_status_and_the_page(self):
        tls.ensure()
        caddyfile = self.path("Caddyfile").read_text()
        self.assertIn("handle /maintenance/status.json", caddyfile)
        self.assertIn("handle_errors 502 503 504", caddyfile)
        network, kiosk = caddyfile.split(":443 {")[1].split(":8443 {")
        self.assertIn("import maintenance", network)
        self.assertIn("import maintenance", kiosk)

    @override_settings(LANGUAGE_CODE="de")
    def test_page_shows_both_languages_default_first(self):
        tls.ensure()
        page = self.path("maintenance.html").read_text()
        self.assertIn('<html lang="de">', page)
        self.assertLess(page.index("Wir sind gleich wieder da"), page.index("We'll be right back"))
