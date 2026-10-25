import csv
import io
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, Member, Order


class OrderExportTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)

    def _rows(self):
        response = self.client.get(reverse("dashboard:export_orders"))
        return list(csv.reader(io.StringIO(response.content.decode())))

    def test_rows_in_order_of_creation_with_sort_name_and_utc_timestamp(self):
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("0.60"), machine_strokes=2,
                             created_at=datetime(2026, 3, 2, 9, 30, tzinfo=dt_timezone.utc), info="jug")
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1,
                             created_at=datetime(2026, 3, 1, 8, 0, tzinfo=dt_timezone.utc))
        rows = self._rows()
        self.assertEqual(rows[0], ["uid", "nickname", "coffee_sort", "value", "machine_strokes", "created_at", "info"])
        self.assertEqual(rows[1], ["42", "Alice", "espresso", "0.30", "1", "2026-03-01T08:00:00+00:00", ""])
        self.assertEqual(rows[2], ["42", "Alice", "espresso", "0.60", "2", "2026-03-02T09:30:00+00:00", "jug"])

    def test_order_without_coffee_sort_exports_an_empty_name(self):
        # Legacy imports can leave the sort unset (see backfill_order_sorts).
        Order.objects.create(member=self.alice, coffee_sort=None, value=Decimal("0.30"), machine_strokes=1)
        self.assertEqual(self._rows()[1][2], "")
