from datetime import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from coffee import services
from coffee.models import CoffeeSort, Member, Order


def at(dt_str: str):
    """A local (TIME_ZONE) wall-clock time, stored as UTC like every other order."""
    return timezone.make_aware(datetime.fromisoformat(dt_str))


class TimeOfDayDistributionTestCase(TestCase):
    def setUp(self):
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.jug = CoffeeSort.objects.create(name="jug", price=Decimal("0.60"), machine_strokes=2)
        self.alice = Member.objects.create(uid=1, nickname="Alice X.")

    def order(self, when: str, sort=None):
        sort = sort or self.espresso
        return Order.objects.create(
            member=self.alice,
            coffee_sort=sort,
            value=sort.price,
            machine_strokes=sort.machine_strokes,
            created_at=at(when),
        )

    def test_hourly_distribution_has_a_slot_for_every_hour(self):
        result = services.hourly_distribution()

        self.assertEqual(result["labels"], [f"{h:02d}" for h in range(24)])
        self.assertEqual(result["cups"], [0] * 24)

    def test_hourly_distribution_groups_by_local_hour(self):
        self.order("2026-01-05 09:15:00")
        self.order("2026-01-06 09:45:00")
        self.order("2026-01-06 14:05:00")

        cups = services.hourly_distribution()["cups"]

        self.assertEqual(cups[9], 2)
        self.assertEqual(cups[14], 1)
        self.assertEqual(sum(cups), 3)

    def test_hourly_distribution_counts_cups_not_orders(self):
        self.order("2026-01-05 09:15:00", sort=self.jug)  # one order, two cups

        self.assertEqual(services.hourly_distribution()["cups"][9], 2)

    @override_settings(TIME_ZONE="Europe/Berlin")
    def test_hours_are_local_not_utc(self):
        """An order pulled at 08:30 Berlin time is stored as 07:30 UTC in winter - it
        belongs in the 08:00 bar, otherwise the whole chart reads an hour off."""
        self.order("2026-01-05 08:30:00")

        cups = services.hourly_distribution()["cups"]

        self.assertEqual(cups[8], 1)
        self.assertEqual(cups[7], 0)

    def test_weekday_distribution_starts_on_monday(self):
        self.order("2026-01-05 09:00:00")  # Monday
        self.order("2026-01-05 10:00:00")  # Monday
        self.order("2026-01-11 09:00:00")  # Sunday

        result = services.weekday_distribution()

        self.assertEqual(result["labels"], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self.assertEqual(result["cups"], [2, 0, 0, 0, 0, 0, 1])

    def test_weekday_distribution_counts_cups_not_orders(self):
        self.order("2026-01-06 09:00:00", sort=self.jug)  # Tuesday, two cups

        self.assertEqual(services.weekday_distribution()["cups"][1], 2)

    def test_orders_without_a_coffee_sort_still_count(self):
        """Legacy orders that could not be matched to a sort are still cups someone
        drew - unlike the sort distribution, this chart must not drop them."""
        Order.objects.create(
            member=self.alice, coffee_sort=None, value=Decimal("0.30"),
            machine_strokes=1, created_at=at("2026-01-05 09:00:00"),
        )

        self.assertEqual(services.hourly_distribution()["cups"][9], 1)
