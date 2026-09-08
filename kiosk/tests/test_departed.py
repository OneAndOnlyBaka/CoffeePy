from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, MemberCard, NfcScan


class DepartedMemberTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice", departed_at=timezone.now())
        self.client.get(reverse("kiosk:index"))

    def _scan(self, uid, event=NfcScan.EVENT_INSERTED):
        NfcScan.objects.create(uid=uid, event=event)

    def test_departed_members_primary_card_is_blocked(self):
        self._scan("2A")  # uid_as_int() -> 42
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Account no longer active")
        self.assertFalse(self.client.session.get("logged_in"))

    def test_departed_members_alt_card_is_also_blocked(self):
        MemberCard.objects.create(member=self.alice, uid=0xDDCCBBAA)
        self._scan("AABBCCDD")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Account no longer active")

    def test_active_member_is_unaffected(self):
        Member.objects.create(uid=99, nickname="Bob")
        self._scan("63", "card_inserted")  # uid_as_int() -> 99
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Hi Bob!")
