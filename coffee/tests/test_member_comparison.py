from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from coffee import services
from coffee.models import CoffeeSort, Member, Order


def at(dt_str: str):
    return timezone.make_aware(datetime.fromisoformat(dt_str))


class MemberVsOthersTestCase(TestCase):
    """The portal's stats page: this member's habits next to everyone else's."""

    def setUp(self):
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.jug = CoffeeSort.objects.create(name="jug", price=Decimal("0.60"), machine_strokes=2)
        self.alice = Member.objects.create(uid=1, nickname="Alice")
        self.bob = Member.objects.create(uid=2, nickname="Bob")
        self.carol = Member.objects.create(uid=3, nickname="Carol")

    def order(self, member, when: str, sort=None):
        sort = sort or self.espresso
        Order.objects.create(
            member=member, coffee_sort=sort, value=sort.price,
            machine_strokes=sort.machine_strokes, created_at=at(when),
        )

    def test_hourly_split_puts_each_side_on_its_own_scale(self):
        """Percent of each side's own total - in absolute cups one member could never be
        compared against two dozen others."""
        self.order(self.alice, "2026-01-05 08:00:00")
        self.order(self.alice, "2026-01-05 09:00:00")
        for _ in range(10):
            self.order(self.bob, "2026-01-05 15:00:00")

        result = services.member_vs_others_hourly(self.alice)

        self.assertEqual(result["you"][8], 50.0)
        self.assertEqual(result["you"][9], 50.0)
        self.assertEqual(result["you"][15], 0.0)
        self.assertEqual(result["others"][15], 100.0)
        self.assertEqual(result["others"][8], 0.0)

    def test_a_member_with_no_orders_gets_zeroes_not_an_error(self):
        self.order(self.bob, "2026-01-05 09:00:00")

        result = services.member_vs_others_hourly(self.alice)

        self.assertEqual(sum(result["you"]), 0)
        self.assertEqual(result["others"][9], 100.0)

    def test_weekday_split_starts_on_monday(self):
        self.order(self.alice, "2026-01-05 09:00:00")  # Monday
        self.order(self.bob, "2026-01-11 09:00:00")  # Sunday

        result = services.member_vs_others_weekday(self.alice)

        self.assertEqual(result["labels"][0], "Mon")
        self.assertEqual(result["you"][0], 100.0)
        self.assertEqual(result["others"][6], 100.0)

    def test_sorts_split_counts_cups_and_orders_by_popularity(self):
        self.order(self.alice, "2026-01-05 09:00:00", sort=self.jug)  # one order, two cups
        for _ in range(3):
            self.order(self.bob, "2026-01-05 09:00:00", sort=self.espresso)
        self.order(self.carol, "2026-01-05 10:00:00", sort=self.espresso)

        result = services.member_vs_others_sorts(self.alice)

        self.assertEqual(result["labels"], ["espresso", "jug"])  # 4 cups vs 2
        self.assertEqual(result["you"], [0.0, 100.0])
        self.assertEqual(result["others"], [100.0, 0.0])

    def test_equally_popular_sorts_keep_a_stable_order(self):
        """Ordering by count alone leaves ties to the set iteration order, which differs
        between processes - the chart's categories would reshuffle between page loads."""
        self.order(self.alice, "2026-01-05 09:00:00", sort=self.jug)  # 2 cups
        for _ in range(2):
            self.order(self.bob, "2026-01-05 09:00:00", sort=self.espresso)  # 2 cups

        self.assertEqual(services.member_vs_others_sorts(self.alice)["labels"], ["espresso", "jug"])

    def test_sorts_split_ignores_orders_without_a_sort(self):
        Order.objects.create(
            member=self.bob, coffee_sort=None, value=Decimal("0.30"),
            machine_strokes=1, created_at=at("2026-01-05 09:00:00"),
        )
        self.order(self.alice, "2026-01-05 09:00:00")

        result = services.member_vs_others_sorts(self.alice)

        self.assertEqual(result["labels"], ["espresso"])

    def test_monthly_compares_against_the_average_active_member(self):
        """Averaging over everyone on file instead would be dragged down by every member
        who stopped coming years ago."""
        month = timezone.localtime().strftime("%Y-%m")
        for _ in range(4):
            self.order(self.alice, timezone.localtime().replace(day=2, hour=9).isoformat(sep=" ")[:19])
        self.order(self.bob, timezone.localtime().replace(day=2, hour=9).isoformat(sep=" ")[:19])
        for _ in range(3):
            self.order(self.carol, timezone.localtime().replace(day=2, hour=9).isoformat(sep=" ")[:19])

        result = services.member_vs_others_monthly(self.alice)
        index = result["labels"].index(month)

        self.assertEqual(result["you"][index], 4)
        self.assertEqual(result["others"][index], 2.0)  # (1 + 3) cups over 2 members

    def test_summary_reports_rank_and_distance_from_average(self):
        for _ in range(6):
            self.order(self.alice, "2026-01-05 09:00:00")
        for _ in range(4):
            self.order(self.bob, "2026-01-05 09:00:00")
        for _ in range(2):
            self.order(self.carol, "2026-01-05 09:00:00")

        summary = services.member_comparison_summary(self.alice)

        self.assertEqual(summary["your_cups"], 6)
        self.assertEqual(summary["others_average"], 3.0)
        self.assertEqual(summary["rank"], 1)
        self.assertEqual(summary["members"], 3)
        self.assertEqual(summary["percent_of_average"], 100.0)

    def test_summary_without_anybody_to_compare_against(self):
        self.order(self.alice, "2026-01-05 09:00:00")

        summary = services.member_comparison_summary(self.alice)

        self.assertEqual(summary["rank"], 1)
        self.assertEqual(summary["members"], 1)
        self.assertIsNone(summary["percent_of_average"])
