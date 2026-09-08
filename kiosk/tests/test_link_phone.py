import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, Member, MemberLinkToken, NfcReaderStatus, NfcScan


class LinkPhonePopupTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        self.client.get(reverse("kiosk:index"))

    def log_in(self):
        session = self.client.session
        session["logged_in"] = True
        session["uid"] = self.alice.uid
        session.save()

    def test_forbidden_when_not_logged_in(self):
        response = self.client.get(reverse("kiosk:link_phone_popup"))
        self.assertEqual(response.status_code, 403)

    def test_generates_a_single_use_token_and_qr(self):
        session = self.client.session
        session["logged_in"] = True
        session["uid"] = self.alice.uid
        session.save()

        response = self.client.get(reverse("kiosk:link_phone_popup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "svg")
        self.assertEqual(MemberLinkToken.objects.filter(member=self.alice).count(), 1)

    def test_wifi_qr_is_offered_alongside_the_link(self):
        """The link points at an address that only exists on the kiosk's own network -
        a phone that hasn't joined it yet scans a code it then can't open."""
        self.log_in()

        with self.settings(AP_SSID="CoffeePy", AP_PASSPHRASE="secret"):
            response = self.client.get(reverse("kiosk:link_phone_popup"))

        self.assertContains(response, "1. Join WiFi")
        self.assertContains(response, "CoffeePy")
        self.assertContains(response, "2. Link phone")

    def test_no_wifi_qr_when_the_kiosk_joins_an_existing_network(self):
        self.log_in()

        with self.settings(AP_SSID="", AP_PASSPHRASE=""):
            response = self.client.get(reverse("kiosk:link_phone_popup"))

        self.assertNotContains(response, "Join WiFi")
        self.assertContains(response, "Link phone")

    def test_reopening_the_popup_replaces_the_previous_token(self):
        session = self.client.session
        session["logged_in"] = True
        session["uid"] = self.alice.uid
        session.save()

        self.client.get(reverse("kiosk:link_phone_popup"))
        first_token = MemberLinkToken.objects.get(member=self.alice).token
        self.client.get(reverse("kiosk:link_phone_popup"))
        self.assertEqual(MemberLinkToken.objects.filter(member=self.alice).count(), 1)
        self.assertNotEqual(MemberLinkToken.objects.get(member=self.alice).token, first_token)


class PanelStateMarkerTestCase(TestCase):
    """The QR modal sits outside #panel so the 500ms poll can't wipe it mid-scan - which
    also means nothing removes it when the panel changes underneath. panel_modal.js closes
    it whenever this marker changes; without it, an auto-logout leaves the "Link this
    phone" login token on screen for whoever walks up next."""

    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=0xDDCCBBAA, nickname="Alice")
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client.get(reverse("kiosk:index"))

    def open_settings(self):
        # Picking a nav button logs the current session out (see views.nav), so the card
        # is tapped after choosing Settings - which is the order a member does it in too.
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel"))  # logs in, on the settings screen

    def test_marker_names_the_screen_and_the_member(self):
        response = self.open_settings()

        self.assertContains(response, f'data-panel-key="settings:{self.alice.uid}"')

    def test_marker_changes_when_the_session_times_out(self):
        before = self.open_settings()

        with mock.patch("time.time", return_value=time.time() + 25):
            after = self.client.get(reverse("kiosk:panel"))

        self.assertContains(after, 'data-panel-key="wait_for_card:"')
        self.assertNotEqual(before.content, after.content)

    def test_marker_is_stable_across_polls_of_the_same_screen(self):
        """Otherwise the modal would close under a member who is still scanning it. Note
        the rest of the settings screen does differ per poll - its csrf token is re-masked
        on every render and the logout countdown ticks - so the marker, not the response,
        is what the modal can be keyed on."""
        self.open_settings()

        second = self.client.get(reverse("kiosk:panel"))

        self.assertContains(second, f'data-panel-key="settings:{self.alice.uid}"')
