import io
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from coffee.models import CoffeeSort, Member, Order

LEGACY_SORTS = """
CREATE TABLE coffee_sorts(coffee_name TEXT, coffee_price REAL, coffee_machine_strokes INT);
CREATE TABLE user(user_uid INTEGER PRIMARY KEY, user_alt_uid INTEGER UNIQUE DEFAULT NULL,
                  user_nick_name TEXT, user_favourite_coffee INT);
CREATE TABLE coffee_order(user_uid INTEGER, order_value REAL, order_coffee_machine_strokes INT,
                          order_timestamp INT, order_datetime TEXT, order_info TEXT);
CREATE TABLE payments(user_uid INTEGER, payment_value REAL, payment_timestamp INT,
                      payment_datetime TEXT, payment_info TEXT);
INSERT INTO coffee_sorts VALUES ('espresso', 0.3, 1);
INSERT INTO coffee_sorts VALUES ('jug', 0.6, 2);
"""


class BackfillOrderSortsTestCase(TestCase):
    """Repairs data imported before import_sqlite read the sort out of the order info."""

    def setUp(self):
        self.espresso = CoffeeSort.objects.create(name="espresso", price="0.30", machine_strokes=1)
        self.jug = CoffeeSort.objects.create(name="jug", price="0.60", machine_strokes=2)
        self.member = Member.objects.create(uid=42, nickname="Alice")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dump = Path(self.tmp.name) / "coffee.db"
        self.dump.write_text(LEGACY_SORTS)

    def legacy_order(self, info):
        return Order.objects.create(
            member=self.member, coffee_sort=None, value="0.30", machine_strokes=1, info=info
        )

    def test_orders_are_matched_through_the_legacy_rowids(self):
        first = self.legacy_order("Ordered ID [1]")
        second = self.legacy_order("Ordered ID [2]")

        call_command("backfill_order_sorts", path=str(self.dump))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.coffee_sort, self.espresso)
        self.assertEqual(second.coffee_sort, self.jug)

    def test_dry_run_changes_nothing(self):
        order = self.legacy_order("Ordered ID [1]")

        call_command("backfill_order_sorts", path=str(self.dump), dry_run=True)

        order.refresh_from_db()
        self.assertIsNone(order.coffee_sort)

    def test_orders_from_the_current_kiosk_are_left_alone(self):
        """Today's kiosk writes "Ordered [<pk>] <name>" - a *Django* pk, not a legacy
        rowid. Such an order can lose its sort when the sort is deleted (SET_NULL), and
        must not be refilled from the legacy numbering."""
        order = self.legacy_order(f"Ordered [{self.jug.pk}] jug")

        call_command("backfill_order_sorts", path=str(self.dump))

        order.refresh_from_db()
        self.assertIsNone(order.coffee_sort)

    def test_orders_that_already_have_a_sort_are_untouched(self):
        order = self.legacy_order("Ordered ID [1]")
        order.coffee_sort = self.jug
        order.save(update_fields=["coffee_sort"])

        call_command("backfill_order_sorts", path=str(self.dump))

        order.refresh_from_db()
        self.assertEqual(order.coffee_sort, self.jug)

    def test_unmatched_rows_are_reported_not_guessed(self):
        self.legacy_order("Ordered ID [99]")

        out = io.StringIO()
        call_command("backfill_order_sorts", path=str(self.dump), stdout=out)

        self.assertIn("0 of 1 legacy orders (1 unmatched)", out.getvalue())
        self.assertEqual(Order.objects.filter(coffee_sort__isnull=True).count(), 1)

    def test_missing_coffee_sort_is_reported(self):
        CoffeeSort.objects.filter(name="jug").delete()
        self.legacy_order("Ordered ID [2]")

        out = io.StringIO()
        call_command("backfill_order_sorts", path=str(self.dump), stdout=out)

        self.assertIn("No coffee sort named 'jug'", out.getvalue())
