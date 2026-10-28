from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, NfcReaderStatus


class ReaderStatusTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.client.get(reverse("kiosk:index"))

    def test_shows_reader_not_available_when_never_reported(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Card reader not available")

    def test_shows_normal_wait_screen_when_online(self):
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Apply your card")
        self.assertNotContains(response, "Card reader not available")

    def test_shows_error_when_explicitly_offline(self):
        NfcReaderStatus.objects.create(pk=1, is_online=False)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Card reader not available")
