""""My coffees" on the phone (coffee/slots.py): the same slots as the kiosk's editor."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee import privacy
from coffee.models import AuditLogEntry, CoffeeSort, Member, MemberSlot


class PortalCoffeesTestCase(TestCase):
    def setUp(self):
        self.kaffee = CoffeeSort.objects.create(name="Kaffee", price=Decimal("0.50"), machine_strokes=1)
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", privacy_version=privacy.VERSION)
        self.client = Client()
        session = self.client.session
        session["portal_member_uid"] = self.member.uid
        session.save()

    def test_saved_from_the_phone(self):
        self.assertContains(self.client.get(reverse("portal:index")), reverse("portal:coffees"))
        response = self.client.post(reverse("portal:coffees"), {
            "s1-sort": self.kaffee.pk, "s1-count": "3", "s1-strength": "7", "favourite": "1", "hide_standard_sorts": "on"})
        self.assertContains(response, "Saved")
        slot = MemberSlot.objects.get(member=self.member)
        # without a machine product nothing but the cups can be set
        self.assertEqual((slot.position, slot.count, slot.strength), (1, 3, None))
        self.member.refresh_from_db()
        self.assertEqual((self.member.favourite_slot, self.member.hide_standard_sorts), (1, True))
        self.assertTrue(AuditLogEntry.objects.filter(action="member.slots_update").exists())

    def test_not_before_the_privacy_notice(self):
        Member.objects.filter(pk=self.member.pk).update(privacy_version=0)
        self.client.post(reverse("portal:coffees"), {"s1-sort": self.kaffee.pk})
        self.assertFalse(MemberSlot.objects.exists())
