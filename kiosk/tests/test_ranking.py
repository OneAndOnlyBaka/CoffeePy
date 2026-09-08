from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, NfcScan, Order


class RankingBadgeTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=1, nickname="Alice")
        self.bob = Member.objects.create(uid=2, nickname="Bob")
        self.carol = Member.objects.create(uid=3, nickname="Carol")
        self.dave = Member.objects.create(uid=4, nickname="Dave")
        for member, strokes in ((self.alice, 10), (self.bob, 8), (self.carol, 6), (self.dave, 4)):
            Order.objects.create(
                member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=strokes
            )
        self.client.get(reverse("kiosk:index"))
        self.client.get(reverse("kiosk:nav", args=["ranking"]))

    def test_alltime_ranking_shows_medals_for_top_three_only(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "🥇")
        self.assertContains(response, "🥈")
        self.assertContains(response, "🥉")
        self.assertEqual(response.content.decode().count("rank-badge"), 3)

    def test_monthly_ranking_also_shows_medals(self):
        self.client.post(reverse("kiosk:ranking_next"))  # alltime -> month
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "🥇")
        self.assertEqual(response.content.decode().count("rank-badge"), 3)

    def test_pillory_view_has_no_medals(self):
        Order.objects.create(
            member=self.dave,
            coffee_sort=self.espresso,
            value=Decimal("50.00"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=40),
        )

        self.client.post(reverse("kiosk:ranking_next"))  # alltime -> month
        self.client.post(reverse("kiosk:ranking_next"))  # month -> pillory
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Dave")
        self.assertNotContains(response, "rank-badge")


class OrderScreenBadgeTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=1, nickname="Alice", favourite_coffee=self.espresso)
        self.bob = Member.objects.create(uid=2, nickname="Bob", favourite_coffee=self.espresso)
        self.dave = Member.objects.create(uid=4, nickname="Dave", favourite_coffee=self.espresso)
        for member, strokes in ((self.alice, 10), (self.bob, 8)):
            Order.objects.create(
                member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=strokes
            )
        self.client.get(reverse("kiosk:index"))

    def _log_in(self, uid_hex):
        NfcScan.objects.create(uid=uid_hex, event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel"))

    def test_top_scorer_sees_both_alltime_and_month_badges(self):
        response = self._log_in("01")  # Alice, uid=1
        self.assertContains(response, "🥇")
        self.assertContains(response, 'title="Top 3 all-time"')
        self.assertContains(response, 'title="Top 3 this month"')

    def test_member_outside_top_three_sees_no_badge(self):
        response = self._log_in("04")  # Dave, uid=4, no orders at all
        self.assertNotContains(response, "rank-badge")

