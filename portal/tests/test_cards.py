from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import AltUidCapture, Member, MemberCard, MemberLinkToken, NfcScan


class PortalCardsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        token = MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(minutes=5))
        self.client.get(reverse("portal:link", args=[token.token]))

    def test_cards_page_lists_primary_uid_and_alt_cards(self):
        MemberCard.objects.create(member=self.alice, uid=99)
        response = self.client.get(reverse("portal:cards"))
        self.assertContains(response, "42")
        self.assertContains(response, "99")

    def test_add_card_arms_a_capture_for_this_member(self):
        self.client.get(reverse("portal:card_add_start"))
        capture = AltUidCapture.objects.get()
        self.assertEqual(capture.member, self.alice)

    def test_add_card_clears_any_other_pending_capture(self):
        bob = Member.objects.create(uid=43, nickname="Bob")
        AltUidCapture.objects.create(member=bob, expires_at=timezone.now() + timedelta(seconds=60))

        self.client.get(reverse("portal:card_add_start"))
        capture = AltUidCapture.objects.get()
        self.assertEqual(capture.member, self.alice)

    def test_poll_waits_while_capture_still_armed(self):
        self.client.get(reverse("portal:card_add_start"))
        response = self.client.get(reverse("portal:card_add_poll"))
        self.assertContains(response, "Waiting for a card")
        self.assertNotIn("HX-Redirect", response.headers)

    def test_poll_redirects_once_kiosk_fulfills_and_deletes_the_capture(self):
        self.client.get(reverse("portal:card_add_start"))
        AltUidCapture.objects.all().delete()  # simulates the kiosk applying it successfully

        response = self.client.get(reverse("portal:card_add_poll"))
        self.assertEqual(response.headers["HX-Redirect"], reverse("portal:cards"))

    def test_poll_reports_timeout_and_cleans_up_the_expired_capture(self):
        AltUidCapture.objects.create(member=self.alice, expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(reverse("portal:card_add_poll"))
        self.assertContains(response, "Timed out")
        self.assertFalse(AltUidCapture.objects.exists())

    def test_poll_shows_a_conflict_error_recorded_by_the_kiosk(self):
        AltUidCapture.objects.create(
            member=self.alice,
            expires_at=timezone.now() + timedelta(seconds=60),
            last_error="That card is already linked to another account - please try a different card.",
        )
        response = self.client.get(reverse("portal:card_add_poll"))
        self.assertContains(response, "already linked to another account")
        self.assertContains(response, "Waiting for a card")

    def test_end_to_end_conflict_is_visible_on_both_portal_and_kiosk(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Carol")
        self.client.get(reverse("portal:card_add_start"))

        kiosk = Client()
        kiosk.get(reverse("kiosk:index"))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)  # Carol's card
        kiosk_response = kiosk.get(reverse("kiosk:panel"))
        portal_response = self.client.get(reverse("portal:card_add_poll"))

        self.assertContains(kiosk_response, "already linked to another account")
        self.assertContains(portal_response, "already linked to another account")
        self.assertTrue(AltUidCapture.objects.exists())  # still armed, so a different card can be tried

    def test_end_to_end_kiosk_actually_links_the_card(self):
        self.client.get(reverse("portal:card_add_start"))
        kiosk = Client()
        kiosk.get(reverse("kiosk:index"))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        response = kiosk.get(reverse("kiosk:panel"))

        self.assertContains(response, "Assigning card to account Alice")
        self.assertTrue(MemberCard.objects.filter(member=self.alice, uid=0xDDCCBBAA).exists())
        self.assertFalse(AltUidCapture.objects.exists())
