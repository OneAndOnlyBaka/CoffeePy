from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee.models import CoffeeSort, Member, NfcScan


@override_settings(PUBLIC_HOST="192.168.4.1")
class QrCodeHostTestCase(TestCase):
    """QR codes are meant to be scanned by a *different* device, so they must never
    embed "localhost" (all a phone would learn from that is to fail to connect) - see
    coffee.network.public_base_url."""

    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.client.get(reverse("kiosk:index"))

    def _urls_passed_to_qr_svg(self, action):
        with mock.patch("kiosk.views._qr_svg") as mock_qr_svg:
            mock_qr_svg.return_value = "<svg></svg>"
            action()
        return [call.args[0] for call in mock_qr_svg.call_args_list]

    def test_setup_wizard_dashboard_qr_uses_public_host(self):
        CoffeeSort.objects.all().delete()  # no coffee sorts yet -> setup wizard state
        urls = self._urls_passed_to_qr_svg(lambda: self.client.get(reverse("kiosk:panel")))
        self.assertTrue(any("192.168.4.1" in u for u in urls))
        self.assertFalse(any("localhost" in u for u in urls))

    def test_info_popup_dashboard_qr_uses_public_host(self):
        urls = self._urls_passed_to_qr_svg(lambda: self.client.get(reverse("kiosk:qr_popup")))
        self.assertTrue(any("192.168.4.1" in u for u in urls))
        self.assertFalse(any("localhost" in u for u in urls))

    def test_link_phone_popup_uses_public_host(self):
        Member.objects.create(uid=42, nickname="Alice X.", favourite_coffee=self.espresso)
        NfcScan.objects.create(uid="2A", event=NfcScan.EVENT_INSERTED)  # uid_as_int() -> 42
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        self.client.get(reverse("kiosk:panel"))  # logs in

        urls = self._urls_passed_to_qr_svg(lambda: self.client.get(reverse("kiosk:link_phone_popup")))
        self.assertTrue(any("192.168.4.1" in u and "/portal/link/" in u for u in urls))
