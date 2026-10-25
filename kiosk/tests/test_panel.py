import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee.models import CoffeeSort, Member, MemberCard, NfcReaderStatus, NfcScan, Order


class KioskPanelTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client.get(reverse("kiosk:index"))  # establishes the session

    def _scan(self, uid, event=NfcScan.EVENT_INSERTED):
        return NfcScan.objects.create(uid=uid, event=event)

    def _member_with_alt_card(self, uid, alt_uid, **kwargs):
        member = Member.objects.create(uid=uid, **kwargs)
        MemberCard.objects.create(member=member, uid=alt_uid)
        return member

    def test_unknown_card_in_order_mode_starts_new_user_settings_flow(self):
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Welcome, new card!")

    def test_known_member_in_order_mode_shows_order_panel(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Hi Tom!")
        self.assertContains(response, "espresso")

    def test_known_member_via_alt_uid_shows_order_panel_with_real_uid_in_session(self):
        # regression: session["uid"] must end up as the member's real pk (42), not the
        # scanned card's own uid_as_int() (0xDDCCBBAA), or every later lookup 404s them.
        self._member_with_alt_card(42, 0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Hi Tom!")
        self.assertEqual(self.client.session["uid"], 42)

    def test_order_confirm_via_alt_uid_records_order_against_the_real_member(self):
        self._member_with_alt_card(42, 0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in

        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "Thank you")
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get().member_id, 42)

    def test_known_member_via_alt_uid_in_settings_mode_shows_real_nickname(self):
        self._member_with_alt_card(42, 0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Hello Tom!")
        self.assertNotContains(response, "Welcome, new card!")

    def test_order_confirm_creates_order_and_logs_out(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in

        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "Thank you")
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get().member_id, 0xDDCCBBAA)

    def test_order_auto_checkout_after_timeout(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in, deadline = now + 20s

        with mock.patch("time.time", return_value=time.time() + 25):
            response = self.client.get(reverse("kiosk:panel"))

        self.assertContains(response, "Thank you")
        self.assertEqual(Order.objects.count(), 1)

    def test_member_deleted_while_logged_in_logs_out_instead_of_failing(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in
        Member.objects.filter(uid=0xDDCCBBAA).delete()  # e.g. merged away on the dashboard

        with mock.patch("time.time", return_value=time.time() + 25):
            response = self.client.get(reverse("kiosk:panel"))

        self.assertContains(response, "Apply your card")
        self.assertEqual(Order.objects.count(), 0)

    def test_nav_resets_session_and_logs_out(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in

        self.client.get(reverse("kiosk:nav", args=["settings"]))
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Apply your card")

    def test_nav_via_htmx_returns_the_new_panel_instead_of_a_redirect(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in

        response = self.client.get(reverse("kiosk:nav", args=["ranking"]), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-mode="ranking"')
        self.assertFalse(self.client.session["logged_in"])

    def test_panel_reports_mode_the_server_switched_to(self):
        # An unknown card in order mode is sent to the settings flow - the nav bar only
        # learns about that from the panel (nav_state.js).
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, 'data-mode="settings"')

    def test_card_not_recognized_for_unparseable_uid(self):
        self._scan(None)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Card not recognized")


@override_settings(AP_SSID="", AP_PASSPHRASE="")
class KioskSetupWizardTestCase(TestCase):
    """No CoffeeSort configured yet: the kiosk should show the QR setup wizard
    immediately, without waiting for a card scan."""

    def setUp(self):
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def test_shows_admin_qr_without_needing_a_scan(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Setup needed")
        self.assertContains(response, "<svg")
        self.assertNotContains(response, "Apply your card")

    def test_no_wifi_qr_when_ap_credentials_unset(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "Join WiFi")

    @override_settings(AP_SSID="CoffeePy", AP_PASSPHRASE="s3cret")
    def test_wifi_qr_shown_when_ap_credentials_set(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Join WiFi")
        self.assertContains(response, 'Network "CoffeePy"')

    def test_wizard_disappears_once_a_coffee_sort_exists(self):
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Apply your card")
        self.assertNotContains(response, "Setup needed")


@override_settings(AP_SSID="CoffeePy", AP_PASSPHRASE="s3cret")
class KioskQrPopupTestCase(TestCase):
    """The round info button on the wait-for-card screen opens the same QR codes as the
    first-run wizard, any time - as a popup, not a nav state."""

    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.client.get(reverse("kiosk:index"))

    def test_wait_for_card_shows_info_button(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "info-btn")
        self.assertContains(response, reverse("kiosk:qr_popup"))

    def test_qr_popup_shows_qr_codes(self):
        response = self.client.get(reverse("kiosk:qr_popup"))
        self.assertContains(response, "Setup &amp; network info")
        self.assertContains(response, "Join WiFi")
        self.assertContains(response, "<svg")
