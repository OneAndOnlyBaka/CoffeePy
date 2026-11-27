"""The privacy notice on the phone (coffee/privacy.py): a phone linked before there was one can look,
but pay, book a purchase or add a card only once its member has confirmed it."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee import privacy
from coffee.models import AltUidCapture, AuditLogEntry, Member, Payment


class PortalPrivacyTestCase(TestCase):
    def setUp(self):
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.")
        self.client = Client()
        session = self.client.session
        session["portal_member_uid"] = self.member.uid
        session.save()

    def test_looking_yes_booking_no(self):
        index = self.client.get(reverse("portal:index"))
        self.assertContains(index, "Please read the privacy notice")
        pay = self.client.post(reverse("portal:pay"), {"amount": "5"})
        self.assertRedirects(pay, reverse("portal:privacy") + "?next=" + reverse("portal:pay"))
        self.assertFalse(Payment.objects.exists())
        self.client.post(reverse("portal:card_add_start"))
        self.assertFalse(AltUidCapture.objects.exists())

    def test_confirmed_on_the_phone(self):
        notice = self.client.get(reverse("portal:privacy") + "?next=" + reverse("portal:pay"))
        self.assertContains(notice, privacy.DOCS_URL)
        response = self.client.post(reverse("portal:privacy") + "?next=" + reverse("portal:pay"))
        self.assertRedirects(response, reverse("portal:pay"))
        self.member.refresh_from_db()
        self.assertTrue(privacy.confirmed(self.member))
        self.assertTrue(AuditLogEntry.objects.filter(action="member.privacy_confirm").exists())
        self.client.post(reverse("portal:pay"), {"amount": "5"})
        self.assertEqual(Payment.objects.get().value, Decimal("5"))
        self.assertNotContains(self.client.get(reverse("portal:index")), "Please read the privacy notice")
        self.assertContains(self.client.get(reverse("portal:privacy")), "You confirmed this on")

    def test_next_only_leads_back_into_the_portal(self):
        response = self.client.post(reverse("portal:privacy") + "?next=https://example.com/")
        self.assertRedirects(response, reverse("portal:index"))
