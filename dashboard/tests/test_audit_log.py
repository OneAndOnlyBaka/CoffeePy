from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry, CoffeeSort, Member, PaymentMethod


class AuditLogTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw", is_superuser=True, is_staff=True)
        self.client.login(username="staffer", password="pw")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:audit_log"))
        self.assertEqual(response.status_code, 302)

    def test_page_lists_entries_newest_first(self):
        AuditLogEntry.objects.create(action="member.create", actor="staffer", target="Alice")
        AuditLogEntry.objects.create(action="member.delete", actor="staffer", target="Bob")
        response = self.client.get(reverse("dashboard:audit_log"))
        self.assertContains(response, "member.create")
        self.assertContains(response, "member.delete")
        actions = [e.action for e in response.context["page"]]
        self.assertEqual(actions[:2], ["member.delete", "member.create"])

    def test_coffee_sort_crud_is_logged(self):
        response = self.client.post(
            reverse("dashboard:coffeesort_add"), {"name": "espresso", "price": "0.30", "machine_strokes": "1"}
        )
        sort = CoffeeSort.objects.get(name="espresso")
        self.assertTrue(AuditLogEntry.objects.filter(action="coffee_sort.create", actor="staffer", target="espresso").exists())

        self.client.post(
            reverse("dashboard:coffeesort_edit", args=[sort.pk]),
            {"name": "espresso", "price": "0.35", "machine_strokes": "1"},
        )
        self.assertTrue(AuditLogEntry.objects.filter(action="coffee_sort.update", target="espresso").exists())

        self.client.post(reverse("dashboard:coffeesort_delete", args=[sort.pk]))
        self.assertTrue(AuditLogEntry.objects.filter(action="coffee_sort.delete", target="espresso").exists())

    def test_payment_method_crud_is_logged(self):
        self.client.post(
            reverse("dashboard:paymentmethod_add"),
            {"name": "PayPal", "provider": "custom", "link_template": "", "is_active": "on", "order": "0"},
        )
        self.assertTrue(AuditLogEntry.objects.filter(action="payment_method.create", target="PayPal").exists())

        method = PaymentMethod.objects.get(name="PayPal")
        self.client.post(reverse("dashboard:paymentmethod_delete", args=[method.pk]))
        self.assertTrue(AuditLogEntry.objects.filter(action="payment_method.delete", target="PayPal").exists())

    def test_member_crud_and_merge_is_logged(self):
        self.client.post(
            reverse("dashboard:member_add"), {"uid": "1", "nickname": "Alice X.", "favourite_coffee": ""}
        )
        self.assertTrue(AuditLogEntry.objects.filter(action="member.create").exists())

        Member.objects.create(uid=2, nickname="Bob X.")
        self.client.post(
            reverse("dashboard:member_merge_confirm", args=[1, 2]),
        )
        self.assertTrue(AuditLogEntry.objects.filter(action="member.merge").exists())

    def test_member_delete_is_logged(self):
        Member.objects.create(uid=5, nickname="Carol X.")
        self.client.post(reverse("dashboard:member_delete", args=[5]))
        self.assertTrue(AuditLogEntry.objects.filter(action="member.delete", target="Carol X. (uid 5)").exists())
