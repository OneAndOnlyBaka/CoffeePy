from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(DEBUG=False)
class ErrorPageTestCase(TestCase):
    """The kiosk can't be reloaded by hand - an error page there has to bring itself back."""

    def test_server_error_on_the_kiosk_recovers_by_itself(self):
        client = Client(raise_request_exception=False)
        with mock.patch("kiosk.views._render_panel", side_effect=RuntimeError("boom")):
            response = client.get(reverse("kiosk:panel"))
        self.assertEqual(response.status_code, 500)
        content = response.content.decode()
        self.assertIn("Kurze Störung", content)
        self.assertIn('location.replace("/kiosk/")', content)
        self.assertIn("/health/", content)

    def test_not_found(self):
        response = Client().get("/kiosk/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Seite nicht gefunden", response.content.decode())

    def test_expired_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("kiosk:pay"), {"amount": "1"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Sitzung abgelaufen", response.content.decode())
