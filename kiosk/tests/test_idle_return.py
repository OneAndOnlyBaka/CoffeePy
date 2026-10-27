import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import AltUidCapture, CoffeeSort, Member, NfcReaderStatus, NfcScan


class KioskIdleReturnTestCase(TestCase):
    """Settings, Pay & Stats and the ranking fall back to ordering after 20s without a tap."""

    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client.get(reverse("kiosk:index"))

    def _poll(self, later=0):
        with mock.patch("time.time", return_value=time.time() + later):
            return self.client.get(reverse("kiosk:panel"))

    def _mode(self, response):
        self.assertEqual(response.status_code, 200)
        return self.client.session["mode"]

    def test_each_mode_returns_to_order_after_idling(self):
        for mode in ("ranking", "settings", "info"):
            with self.subTest(mode=mode):
                self.client.get(reverse("kiosk:nav", args=[mode]))
                self.assertEqual(self._mode(self._poll(10)), mode)
                response = self._poll(25)
                self.assertEqual(self._mode(response), "order")
                self.assertContains(response, 'data-mode="order"')

    def test_order_mode_stays(self):
        self.client.get(reverse("kiosk:nav", args=["order"]))
        self.assertEqual(self._mode(self._poll(60)), "order")

    def test_ranking_tab_restarts_the_countdown_and_resets_to_alltime(self):
        self.client.get(reverse("kiosk:nav", args=["ranking"]))
        with mock.patch("time.time", return_value=time.time() + 15):
            self.client.get(reverse("kiosk:ranking_view", args=["month"]))
        self.assertEqual(self._mode(self._poll(25)), "ranking")
        self.assertEqual(self._mode(self._poll(40)), "order")
        self.assertEqual(self.client.session["ranking_view"], "alltime")

    def test_countdown_starts_after_a_logout(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=self.espresso)
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        self._poll(15)  # logs in
        self.assertTrue(self.client.session["logged_in"])
        self._poll(40)  # auto-logout, back on settings' "apply your card"
        self.assertEqual(self.client.session["mode"], "settings")
        self.assertFalse(self.client.session["logged_in"])
        self.assertEqual(self._mode(self._poll(50)), "settings")
        self.assertEqual(self._mode(self._poll(65)), "order")

    def test_refused_card_counts_as_a_tap(self):
        self.client.get(reverse("kiosk:nav", args=["info"]))
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", departed_at=timezone.now())
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        self._poll(15)
        self.assertEqual(self._mode(self._poll(30)), "info")

    def test_not_while_a_card_is_being_added(self):
        member = Member.objects.create(uid=42, nickname="Alice X.")
        AltUidCapture.objects.create(member=member, expires_at=timezone.now() + timedelta(seconds=120))
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        self.assertEqual(self._mode(self._poll(60)), "settings")

    def test_session_without_a_start_counts_from_now(self):
        session = self.client.session
        session["mode"] = "ranking"
        session.pop("idle_since", None)
        session.save()
        self.assertEqual(self._mode(self._poll(0)), "ranking")
        self.assertEqual(self._mode(self._poll(25)), "order")
