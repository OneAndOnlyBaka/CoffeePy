import re
import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, Member, NfcReaderStatus, NfcScan, Order


class StablePanelTestCase(TestCase):
    """A logged-in screen returns the same bytes on every poll until something happens: then
    panel_stable_swap.js skips the swap, and Chromium never tears the panel down - where its
    renderer crashes now and then (CLAUDE.md; measured: two crashes in three orders while the
    countdown and the csrf token made every poll differ)."""

    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.")
        self.client.get(reverse("kiosk:index"))

    def log_in(self, mode):
        self.client.get(reverse("kiosk:nav", args=[mode]))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel")).content

    def test_logged_in_screens_are_identical_from_poll_to_poll(self):
        for mode, marker in (("order", b"Hi Tom X.!"), ("info", b"Cups in total"), ("settings", b"Hello Tom X.!")):
            with self.subTest(mode):
                first = self.log_in(mode)
                self.assertIn(marker, first)
                self.assertIn(b"data-deadline=", first)
                self.assertNotIn(b"csrfmiddlewaretoken", first)
                later = time.time() + 3
                with mock.patch("time.time", return_value=later):
                    self.assertEqual(first, self.client.get(reverse("kiosk:panel")).content)
                self.client.get(reverse("kiosk:nav", args=["order"]))  # logs out

    def test_the_countdown_carries_its_text_and_moment(self):
        content = self.log_in("order").decode()
        deadline = int(re.search(r'data-deadline="(\d+)"', content).group(1))
        self.assertAlmostEqual(deadline / 1000, time.time() + 20, delta=2)
        self.assertIn('data-text="Automatic checkout in {s}s"', content)

    def test_the_csrf_token_comes_from_the_page(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get(reverse("kiosk:index")).content.decode()
        token = re.search(r'"X-CSRFToken": "([^"]+)"', page).group(1)
        client.get(reverse("kiosk:nav", args=["order"]))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        client.get(reverse("kiosk:panel"))
        self.assertEqual(client.post(reverse("kiosk:order_confirm")).status_code, 403)
        client.post(reverse("kiosk:order_confirm"), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(Order.objects.count(), 1)
