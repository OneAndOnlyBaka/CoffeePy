from decimal import Decimal

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee.models import CoffeeSort, NfcReaderStatus


class DeviceLanguageTestCase(TestCase):
    """config/language.py: the device's cookie, else the default chosen at setup - never the
    browser's own language."""

    def setUp(self):
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def panel(self, **headers):
        return self.client.get(reverse("kiosk:panel"), **headers)

    @override_settings(LANGUAGE_CODE="de")
    def test_default_from_setup_applies_without_a_cookie(self):
        self.assertEqual(self.panel()["Content-Language"], "de")

    @override_settings(LANGUAGE_CODE="de")
    def test_browser_language_is_ignored(self):
        self.assertEqual(self.panel(HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")["Content-Language"], "de")

    @override_settings(LANGUAGE_CODE="de")
    def test_device_cookie_wins_over_the_default(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        self.assertEqual(self.panel()["Content-Language"], "en")

    def test_unknown_cookie_value_falls_back_to_the_default(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "fr"
        self.assertEqual(self.panel()["Content-Language"], settings.LANGUAGE_CODE)

    @override_settings(LANGUAGE_CODE="en")
    def test_kiosk_button_switches_to_the_other_language_and_back(self):
        response = self.client.get(reverse("kiosk:language"))
        self.assertRedirects(response, reverse("kiosk:index"), fetch_redirect_response=False)
        self.assertEqual(self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value, "de")
        self.assertEqual(self.panel()["Content-Language"], "de")
        self.client.get(reverse("kiosk:language"))
        self.assertEqual(self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

    def test_wait_screen_shows_the_button_labelled_with_the_other_language(self):
        self.assertContains(self.panel(), 'class="info-btn lang-btn"')
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        self.assertContains(self.panel(), ">EN</a>")

    def test_wait_screen_with_language_button_is_identical_from_poll_to_poll(self):
        # The reason the kiosk has its own GET button: a set_language form's csrf token
        # would differ per render and restart the wait screen's animation every poll.
        self.assertEqual(self.panel().content, self.panel().content)
