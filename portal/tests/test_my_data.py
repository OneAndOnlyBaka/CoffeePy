import json
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import AuditLogEntry, Member, MemberLinkToken, Payment
from dashboard import audit


class MyDataTestCase(TestCase):
    def setUp(self):
        self.alice = Member.objects.create(uid=42, nickname="Alice X.")
        Member.objects.create(uid=99, nickname="Bob X.")
        self.phone = Client()
        token = MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(minutes=5))
        self.phone.get(reverse("portal:link", args=[token.token]))
        Payment.objects.create(member=self.alice, value=Decimal("2.00"))
        # An admin corrected a booking of hers - the admin's name and address are not hers to see.
        AuditLogEntry.objects.create(action="payment.update", actor="chef", target=audit.member_actor(self.alice),
                                     source=AuditLogEntry.SOURCE_DASHBOARD, ip="10.0.0.7")

    def download(self):
        response = self.phone.get(reverse("portal:my_data"))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_her_own_data(self):
        data = self.download()
        self.assertEqual(data["member"]["name"], "Alice X.")
        self.assertEqual([p["value"] for p in data["payments"]], ["2.00"])
        self.assertEqual(len(data["phones"]), 1)

    def test_other_people_by_role_only(self):
        entry = next(e for e in self.download()["audit_log"] if e["action"] == "payment.update")
        self.assertEqual((entry["actor"], entry["address"]), ("dashboard", None))
        own = next(e for e in self.download()["audit_log"] if e["action"] == "portal.login")
        self.assertEqual(own["actor"], "Alice X. (uid 42)")

    def test_audited_and_offered_on_the_start_page(self):
        self.download()
        self.assertTrue(AuditLogEntry.objects.filter(action="member.export", actor="Alice X. (uid 42)").exists())
        self.assertContains(self.phone.get(reverse("portal:index")), reverse("portal:my_data"))

    def test_needs_a_linked_phone(self):
        self.assertNotEqual(Client().get(reverse("portal:my_data")).status_code, 200)
