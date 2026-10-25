import ipaddress

from django.test import RequestFactory, TestCase, override_settings

from coffee.network import detect_local_ip, public_base_url, public_host


class DetectLocalIpTestCase(TestCase):
    def test_returns_a_valid_ip_address(self):
        # Can't assert a specific value (depends on the host running the test), just
        # that it's a real, parseable address rather than blowing up.
        ipaddress.ip_address(detect_local_ip())


class PublicHostTestCase(TestCase):
    @override_settings(PUBLIC_HOST="192.168.4.1")
    def test_explicit_public_host_wins(self):
        self.assertEqual(public_host(), "192.168.4.1")

    @override_settings(PUBLIC_HOST="")
    def test_falls_back_to_autodetection_when_unset(self):
        ipaddress.ip_address(public_host())


class PublicBaseUrlTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(PUBLIC_HOST="192.168.4.1")
    def test_swaps_localhost_host_header_for_public_host(self):
        request = self.factory.get("/kiosk/", SERVER_NAME="localhost", SERVER_PORT="8000")
        self.assertEqual(public_base_url(request), "http://192.168.4.1:8000")

    @override_settings(PUBLIC_HOST="192.168.4.1")
    def test_omits_default_http_port(self):
        request = self.factory.get("/kiosk/", SERVER_NAME="localhost", SERVER_PORT="80")
        self.assertEqual(public_base_url(request), "http://192.168.4.1")

    @override_settings(PUBLIC_HOST="192.168.4.1")
    def test_behind_caddy_drops_the_kiosk_only_port(self):
        # The kiosk reaches Caddy on 127.0.0.1:8443, which no phone can - its QR codes have
        # to point at the network side, https on the default port.
        request = self.factory.get("/kiosk/", SERVER_NAME="localhost", SERVER_PORT="8443", secure=True)
        self.assertEqual(public_base_url(request), "https://192.168.4.1")
