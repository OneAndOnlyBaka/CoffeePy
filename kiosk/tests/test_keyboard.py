import re
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

from coffee.models import CoffeeSort, Member, NfcScan


class OnScreenKeyboardTestCase(TestCase):
    """The keyboard is an overlay on the kiosk page (static/kiosk/osk.js), not a page of its own:
    on the device, the navigation to the old /kiosk/keyboard/ page and back crashed Chromium's
    renderer (2026-10-31). Its behaviour is the browser's; what is checked here is that the
    page carries it and nothing leads off the page any more."""

    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.")
        self.page = self.client.get(reverse("kiosk:index")).content.decode()

    def panel_in(self, mode):
        self.client.get(reverse("kiosk:nav", args=[mode]))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel")).content.decode()

    def test_the_page_carries_the_keyboard_outside_the_panel(self):
        self.assertIn('id="osk-modal"', self.page)
        self.assertIn("kiosk/osk.js", self.page)
        panel = re.search(r'<main id="panel".*?</main>', self.page, re.S).group(0)
        self.assertNotIn("osk-modal", panel)

    def test_the_keyboard_buttons_name_their_field(self):
        settings = self.panel_in("settings")
        self.assertIn('data-osk-for="nickname-input"', settings)
        self.assertIn('id="nickname-input"', settings)
        self.client.get(reverse("kiosk:nav", args=["order"]))
        pay = self.panel_in("info")
        self.assertRegex(pay, r'data-osk-for="pay-amount-input" data-osk-numeric')
        self.assertIn('id="pay-amount-input"', pay)

    def test_no_keyboard_page_any_more(self):
        with self.assertRaises(NoReverseMatch):
            reverse("kiosk:keyboard")
        self.assertNotIn("/kiosk/keyboard/", self.panel_in("settings"))

    def test_a_refused_name_stays_in_the_field(self):
        self.panel_in("settings")
        self.client.post(reverse("kiosk:settings_save"), {"nickname": "Test"})  # a placeholder
        self.assertIn('value="Test"', self.client.get(reverse("kiosk:panel")).content.decode())
        self.assertEqual(Member.objects.get(uid=0xDDCCBBAA).nickname, "Tom X.")


class FavouriteTilesTestCase(TestCase):
    """The favourite coffee is a set of tiles (radio buttons), not a <select>: its dropdown is a
    popup window of its own, and on the device nobody could pick from it (2026-10-31)."""

    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.lungo = CoffeeSort.objects.create(name="lungo", price=Decimal("0.40"), machine_strokes=1)
        self.client.get(reverse("kiosk:index"))
        self.client.get(reverse("kiosk:nav", args=["settings"]))

    def settings_panel(self):
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel")).content.decode()

    def checked(self, content):
        return re.findall(r'value="(\d+)"\s+checked', content)

    def test_a_new_card_starts_on_the_first_sort(self):
        content = self.settings_panel()
        self.assertNotIn("<select", content)
        self.assertEqual(self.checked(content), [str(self.espresso.pk)])

    def test_a_refused_name_keeps_the_pick(self):
        self.settings_panel()
        response = self.client.post(reverse("kiosk:settings_save"), {"nickname": "Test", "favourite_coffee_id": self.lungo.pk})
        self.assertEqual(self.checked(response.content.decode()), [str(self.lungo.pk)])
        self.client.post(reverse("kiosk:settings_save"), {"nickname": "Tom K.", "favourite_coffee_id": self.lungo.pk})
        self.assertEqual(Member.objects.get(uid=0xDDCCBBAA).favourite_coffee, self.lungo)
        self.assertNotIn("pending_favourite", self.client.session)
