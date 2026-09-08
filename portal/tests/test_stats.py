import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, MemberLinkToken, Order


class PortalStatsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        self.bob = Member.objects.create(uid=43, nickname="Bob")
        token = MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(minutes=5))
        self.client.get(reverse("portal:link", args=[token.token]))

    def order(self, member, when: str):
        Order.objects.create(
            member=member, coffee_sort=self.espresso, value=self.espresso.price, machine_strokes=1,
            created_at=timezone.make_aware(datetime.fromisoformat(when)),
        )

    def test_requires_a_linked_session(self):
        self.client.cookies.clear()
        response = self.client.get(reverse("portal:stats"))
        self.assertNotEqual(response.status_code, 200)

    def test_page_renders_the_comparison_charts(self):
        response = self.client.get(reverse("portal:stats"))

        self.assertContains(response, "You vs the others")
        for canvas in ("monthlyChart", "hourlyChart", "weekdayChart", "sortsChart"):
            self.assertContains(response, canvas)

    def test_charts_separate_this_member_from_everyone_else(self):
        self.order(self.alice, "2026-01-05 08:00:00")
        self.order(self.bob, "2026-01-05 16:00:00")

        response = self.client.get(reverse("portal:stats"))

        hourly = json.loads(response.context["hourly"])
        self.assertEqual(hourly["you"][8], 100.0)
        self.assertEqual(hourly["others"][16], 100.0)
        self.assertEqual(hourly["you"][16], 0.0)

    def test_another_members_orders_never_show_up_as_your_own(self):
        """The portal is a member-facing page - the others only ever appear aggregated."""
        self.order(self.bob, "2026-01-05 16:00:00")

        response = self.client.get(reverse("portal:stats"))

        self.assertEqual(response.context["summary"]["your_cups"], 0)
        self.assertNotContains(response, "Bob")

    def test_a_brand_new_member_sees_the_page_without_errors(self):
        response = self.client.get(reverse("portal:stats"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["summary"]["percent_of_average"])
