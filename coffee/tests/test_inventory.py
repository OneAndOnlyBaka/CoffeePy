from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from coffee import purge, services
from coffee.models import (
    PURCHASE_METHOD_NAME,
    CoffeeSort,
    InventoryItem,
    Member,
    Order,
    Payment,
    Purchase,
    StockCount,
)


class InventoryServicesTestCase(TestCase):
    """A purchase is a payment in kind: its full price goes into the balance through a Payment, and
    the stock is the last count plus what was bought since."""

    def setUp(self):
        self.member = Member.objects.create(uid=1, nickname="Alice X.")
        self.coffee = InventoryItem.objects.create(name="Coffee beans", unit="kg")
        self.milk = InventoryItem.objects.create(name="Milk", unit="l")

    def test_a_purchase_is_credited_in_full(self):
        Order.objects.create(member=self.member, value=Decimal("20.00"), machine_strokes=1)
        purchase = services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        self.assertEqual(services.account_balance(self.member), Decimal("-5.01"))
        self.assertEqual(purchase.payment.method_name, PURCHASE_METHOD_NAME)
        self.assertEqual(purchase.payment.value, Decimal("14.99"))
        self.assertEqual(services.describe_purchase(purchase), f"#{purchase.pk}: 1 kg Coffee beans, 14.99 EUR")

    def test_an_edit_moves_the_credit_along_and_a_delete_takes_it(self):
        other = Member.objects.create(uid=2, nickname="Bob Y.")
        purchase = services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        purchase.member, purchase.price = other, Decimal("9.50")
        services.update_purchase(purchase)
        self.assertEqual(services.account_balance(self.member), Decimal("0"))
        self.assertEqual(services.account_balance(other), Decimal("9.50"))
        services.delete_purchase(purchase)
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_stock_is_the_last_count_plus_what_was_bought_since(self):
        services.book_purchase(self.member, self.milk, Decimal("6"), Decimal("6.00"),
                               created_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(services.stock_levels()[1]["stock"], Decimal("6"))
        count = services.record_stock_count(self.milk, Decimal("2"), "Alice X.")
        StockCount.objects.filter(pk=count.pk).update(created_at=timezone.now() - timedelta(minutes=1))
        services.book_purchase(self.member, self.milk, Decimal("1.5"), Decimal("1.50"))
        level = services.stock_levels()[1]
        self.assertEqual((level["item"], level["stock"], level["bought_since"]), (self.milk, Decimal("3.5"), Decimal("1.5")))
        self.assertEqual(services.stock_levels()[0]["stock"], Decimal("0"))

    def test_quantities_are_typed_either_way(self):
        self.assertEqual(services.parse_quantity("1,5"), Decimal("1.500"))
        self.assertEqual(services.parse_quantity(" 2 "), Decimal("2.000"))
        for bad in ("", "abc", "-1", "NaN", "Infinity", "1e9"):
            self.assertIsNone(services.parse_quantity(bad), bad)

    def test_money_flow_shows_purchases_apart_from_payments(self):
        Payment.objects.create(member=self.member, value=Decimal("5.00"))
        services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        totals = services.monthly_totals()
        self.assertEqual(totals["payments"][-1], 5.0)
        self.assertEqual(totals["purchases"][-1], 14.99)
        services.book_purchase(self.member, self.milk, Decimal("1"), Decimal("0.99"))
        spending = services.purchase_totals()
        self.assertEqual([(item["name"], item["sum"]) for item in spending["items"]],
                         [("Coffee beans", Decimal("14.99")), ("Milk", Decimal("0.99"))])
        self.assertEqual([(month["totals"], month["sum"]) for month in spending["months"]],
                         [([Decimal("14.99"), Decimal("0.99")], Decimal("15.98"))])
        self.assertEqual(spending["sum"], Decimal("15.98"))

    def test_member_data_lists_the_purchases(self):
        services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        data = services.member_data(self.member)
        self.assertEqual(data["purchases"][0]["item"], "Coffee beans")
        self.assertEqual(data["payments"][0]["method"], PURCHASE_METHOD_NAME)

    def test_a_reset_takes_everything_an_import_the_purchases_but_not_the_items(self):
        services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        services.record_stock_count(self.coffee, Decimal("1"), "Alice X.")
        purge.purge(purge.IMPORT_SCOPE)
        self.assertFalse(Purchase.objects.exists() or StockCount.objects.exists())
        self.assertEqual(InventoryItem.objects.count(), 2)
        purge.purge(purge.FULL_SCOPE)
        self.assertFalse(InventoryItem.objects.exists())


class CostPerCupTestCase(TestCase):
    """The price suggestion (docs/CALCULATIONS.md): what a cup cost, per stroke - a jug is two."""

    def setUp(self):
        self.member = Member.objects.create(uid=1, nickname="Alice X.")
        self.coffee = InventoryItem.objects.create(name="Coffee beans", unit="kg")
        self.jug = CoffeeSort.objects.create(name="jug", price=Decimal("0.80"), machine_strokes=2)
        self.cup = CoffeeSort.objects.create(name="cup", price=Decimal("0.40"), machine_strokes=1)
        self.now = timezone.now()

    def jugs(self, n, days_ago=1):
        Order.objects.bulk_create(Order(member=self.member, coffee_sort=self.jug, value=self.jug.price,
                                        machine_strokes=2, created_at=self.now - timedelta(days=days_ago))
                                  for _ in range(n))

    def buy(self, days_ago, quantity="1", price="15.00"):
        services.book_purchase(self.member, self.coffee, Decimal(quantity), Decimal(price),
                               created_at=self.now - timedelta(days=days_ago))

    def test_purchases_over_cups_counted_in_strokes(self):
        self.jugs(60)  # 120 cups, 48 EUR
        for days in (100, 70, 40, 10):
            self.buy(days)
        data = services.cost_per_cup(12)
        self.assertEqual(data["cups"], 120)
        self.assertEqual(data["per_cup"], Decimal("0.5"))
        self.assertEqual(data["per_cup_cents"], Decimal("50.0"))
        self.assertFalse(data["counted"])
        self.assertTrue(data["enough_data"])
        self.assertEqual(data["balance"], Decimal("-12.00"))
        suggestions = {row["sort"].name: row for row in services.price_suggestions(data["per_cup"], 10)}
        self.assertEqual(suggestions["cup"]["suggestion"], Decimal("0.55"))
        self.assertEqual(suggestions["jug"]["suggestion"], Decimal("1.10"))
        self.assertEqual(suggestions["jug"]["difference_percent"], Decimal("38"))

    def test_suggestions_are_rounded_up_to_five_cents(self):
        rows = {row["sort"].name: row["suggestion"] for row in services.price_suggestions(Decimal("0.4"), 10)}
        self.assertEqual(rows["cup"], Decimal("0.45"))  # 0.44
        self.assertEqual(rows["jug"], Decimal("0.90"))  # 0.88

    def test_the_adjustment_may_go_down(self):
        rows = {row["sort"].name: row["suggestion"] for row in services.price_suggestions(Decimal("0.4"), -25)}
        self.assertEqual(rows["cup"], Decimal("0.30"))
        self.assertEqual(rows["jug"], Decimal("0.60"))
        rows = {row["sort"].name: row["suggestion"] for row in services.price_suggestions(Decimal("0.4"), -100)}
        self.assertEqual(rows["cup"], Decimal("0"))

    def test_two_stock_takes_count_what_was_used_not_what_was_bought(self):
        StockCount.objects.create(item=self.coffee, quantity=Decimal("2"), created_at=self.now - timedelta(days=90))
        self.buy(80)
        self.buy(5, quantity="3", price="45.00")  # after the last count: still on the shelf
        StockCount.objects.create(item=self.coffee, quantity=Decimal("1"), created_at=self.now - timedelta(days=10))
        self.jugs(50, days_ago=30)  # between the counts: 100 cups
        self.jugs(50, days_ago=2)   # after the last count: not this stretch's
        row = services.cost_per_cup(12)["items"][0]
        self.assertTrue(row["counted"])
        self.assertEqual(row["used"], Decimal("2"))
        self.assertEqual(row["cost"], Decimal("30.00"))  # 2 kg at 15 EUR/kg
        self.assertEqual(row["cups"], 100)
        self.assertEqual(row["per_cup"], Decimal("0.3"))
        self.assertEqual(row["spent"], Decimal("60.00"))

    def test_too_little_data_suggests_nothing(self):
        self.jugs(60)
        self.buy(10)
        self.assertFalse(services.cost_per_cup(12)["enough_data"])  # one month of purchases
        for days in (100, 70):
            self.buy(days)
        Order.objects.all().delete()
        self.jugs(40)  # 80 cups
        self.assertFalse(services.cost_per_cup(12)["enough_data"])

    def test_what_departed_members_owed_is_written_off(self):
        self.jugs(60)
        self.member.departed_at = self.now - timedelta(days=1)
        self.member.save()
        Payment.objects.create(member=self.member, value=Decimal("8.00"), method_name="Cash")
        data = services.cost_per_cup(12)
        self.assertEqual(data["written_off"], Decimal("40.00"))
        self.assertEqual(data["written_off_percent"], Decimal("83.3"))


class CoffeeBoxPurchaseTestCase(TestCase):
    def test_counted_in_the_statistics_but_in_no_balance(self):
        member = Member.objects.create(uid=1, nickname="Alice X.")
        coffee = InventoryItem.objects.create(name="Coffee beans", unit="kg")
        services.book_purchase(None, coffee, Decimal("1"), Decimal("15.00"))
        services.book_purchase(member, coffee, Decimal("1"), Decimal("5.00"))
        monthly = services.monthly_totals()
        self.assertEqual(monthly["purchases"][-1], 20.0)
        self.assertEqual(monthly["payments"][-1], 0.0)
        self.assertEqual(services.account_balance(member), Decimal("5.00"))
        self.assertEqual(Payment.objects.count(), 1)
