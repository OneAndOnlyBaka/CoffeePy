from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import AltUidCapture, CoffeeSort, Member, MemberCard, NfcScan


class AltUidCaptureKioskTestCase(TestCase):
    """The dashboard arms an AltUidCapture from its own browser session; the kiosk (a
    different session) is what actually watches for and applies the next scan."""

    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        self.client.get(reverse("kiosk:index"))

    def _arm(self, member, seconds=60):
        return AltUidCapture.objects.create(member=member, expires_at=timezone.now() + timedelta(seconds=seconds))

    def _scan(self, uid, event=NfcScan.EVENT_INSERTED):
        return NfcScan.objects.create(uid=uid, event=event)

    def test_shows_assigning_message_without_needing_a_scan(self):
        self._arm(self.alice)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Assigning card to account Alice")

    def test_tapping_a_card_sets_the_alt_uid_and_clears_the_capture(self):
        self._arm(self.alice)
        self._scan("AABBCCDD")  # uid_as_int() -> 0xDDCCBBAA
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Assigning card to account Alice")

        self.alice.refresh_from_db()
        self.assertTrue(MemberCard.objects.filter(member=self.alice, uid=0xDDCCBBAA).exists())
        self.assertFalse(AltUidCapture.objects.exists())

    def test_scanning_own_uid_is_ignored_and_capture_stays_armed(self):
        member = Member.objects.create(uid=0xDDCCBBAA, nickname="Bob")
        self._arm(member)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))

        member.refresh_from_db()
        self.assertFalse(MemberCard.objects.filter(member=member).exists())
        self.assertTrue(AltUidCapture.objects.exists())

    def test_scanning_a_card_already_in_use_is_ignored_and_capture_stays_armed(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Carol")
        self._arm(self.alice)
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))

        self.alice.refresh_from_db()
        self.assertFalse(MemberCard.objects.filter(member=self.alice).exists())
        self.assertTrue(AltUidCapture.objects.exists())
        self.assertContains(response, "already linked to another account")

    def test_conflict_error_keeps_showing_on_later_polls_without_a_new_scan(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Carol")
        self._arm(self.alice)
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # records the conflict

        response = self.client.get(reverse("kiosk:panel"))  # no new scan this time
        self.assertContains(response, "Assigning card to account Alice")
        self.assertContains(response, "already linked to another account")

    def test_a_later_successful_tap_clears_the_conflict_along_with_the_capture(self):
        Member.objects.create(uid=0xDDCCBBAA, nickname="Carol")
        self._arm(self.alice)
        self._scan("AABBCCDD")  # conflict with Carol's card
        self.client.get(reverse("kiosk:panel"))

        self._scan("11223344")  # a fresh, unclaimed card
        self.client.get(reverse("kiosk:panel"))
        self.assertTrue(MemberCard.objects.filter(member=self.alice, uid=0x44332211).exists())
        self.assertFalse(AltUidCapture.objects.exists())

    def test_expired_capture_is_ignored_by_the_kiosk_and_left_for_the_dashboard_to_report(self):
        self._arm(self.alice, seconds=-1)
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))

        self.assertNotContains(response, "Assigning card to account Alice")
        self.alice.refresh_from_db()
        self.assertFalse(MemberCard.objects.filter(member=self.alice).exists())
        # left in place (expired, not deleted) so the dashboard poll can report a timeout
        self.assertTrue(AltUidCapture.objects.exists())

    def test_capture_takes_priority_over_the_no_coffee_sorts_wizard(self):
        CoffeeSort.objects.all().delete()
        self._arm(self.alice)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Assigning card to account Alice")
        self.assertNotContains(response, "Setup needed")

    def test_a_member_can_have_more_than_one_alt_card(self):
        MemberCard.objects.create(member=self.alice, uid=0x11111111)
        self._arm(self.alice)
        self._scan("AABBCCDD")  # uid_as_int() -> 0xDDCCBBAA, a second alt card
        self.client.get(reverse("kiosk:panel"))

        self.assertEqual(set(self.alice.alt_cards.values_list("uid", flat=True)), {0x11111111, 0xDDCCBBAA})
