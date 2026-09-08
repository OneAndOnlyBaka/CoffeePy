from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, Member, NfcScan


class KeyboardEditTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.client.get(reverse("kiosk:index"))

    def _scan_and_login(self, uid="AABBCCDD"):
        NfcScan.objects.create(uid=uid, event=NfcScan.EVENT_INSERTED)
        self.client.get(reverse("kiosk:panel"))  # consumes the scan, logs in

    def test_redirects_when_not_logged_in(self):
        response = self.client.get(reverse("kiosk:keyboard"), {"field": "nickname"})
        self.assertRedirects(response, reverse("kiosk:index"))

    def test_redirects_on_unknown_field(self):
        self._scan_and_login()
        response = self.client.get(reverse("kiosk:keyboard"), {"field": "bogus"})
        self.assertRedirects(response, reverse("kiosk:index"))

    def test_get_renders_form_prefilled_with_value(self):
        self._scan_and_login()
        response = self.client.get(reverse("kiosk:keyboard"), {"field": "nickname", "value": "Tom"})
        self.assertContains(response, 'value="Tom"')
        self.assertContains(response, "Done")

    def test_post_nickname_prefills_settings_panel(self):
        self._scan_and_login()
        response = self.client.post(reverse("kiosk:keyboard"), {"field": "nickname", "value": "Alice"})
        self.assertRedirects(response, reverse("kiosk:index"))

        panel = self.client.get(reverse("kiosk:panel"))
        self.assertContains(panel, 'value="Alice"')

    def test_settings_save_persists_the_pending_nickname_and_clears_it(self):
        self._scan_and_login()
        self.client.post(reverse("kiosk:keyboard"), {"field": "nickname", "value": "Alice"})

        self.client.post(reverse("kiosk:settings_save"), {"nickname": "Alice"})
        self.assertEqual(Member.objects.get(uid=0xDDCCBBAA).nickname, "Alice")

        # a fresh session for a *different* card must not see the old pending value
        self._scan_and_login(uid="11223344")
        panel = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(panel, 'value="Alice"')

    def test_post_amount_prefills_pay_panel(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom")
        self.client.get(reverse("kiosk:nav", args=["info"]))
        self._scan_and_login()

        response = self.client.post(reverse("kiosk:keyboard"), {"field": "amount", "value": "5.50"})
        self.assertRedirects(response, reverse("kiosk:index"))

        panel = self.client.get(reverse("kiosk:panel"))
        self.assertContains(panel, 'value="5.50"')

    def test_pay_clears_pending_amount(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom")
        self.client.get(reverse("kiosk:nav", args=["info"]))
        self._scan_and_login()
        self.client.post(reverse("kiosk:keyboard"), {"field": "amount", "value": "5.50"})

        self.client.post(reverse("kiosk:pay"), {"amount": "5.50"})

        panel = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(panel, 'value="5.50"')
