from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from coffee import services
from coffee.models import CoffeeSort, Member, Order, Payment, PaymentMethod


class ServicesTestCase(TestCase):
    def setUp(self):
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.jug = CoffeeSort.objects.create(name="jug", price=Decimal("0.60"), machine_strokes=2)
        self.alice = Member.objects.create(uid=1, nickname="Alice", favourite_coffee=self.espresso)
        self.bob = Member.objects.create(uid=2, nickname="Bob", favourite_coffee=self.jug)

    def test_total_and_month_cups(self):
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        Order.objects.create(
            member=self.alice,
            coffee_sort=self.espresso,
            value=Decimal("0.30"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=60),
        )
        self.assertEqual(services.total_cups(self.alice), 2)
        self.assertEqual(services.month_cups(self.alice), 1)

    def test_account_balance(self):
        Payment.objects.create(member=self.alice, value=Decimal("5.00"))
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        self.assertEqual(services.account_balance(self.alice), Decimal("4.70"))

    def test_deposit_pillory_only_lists_negative_balances_before_this_month(self):
        # Alice: ordered heavily last month, never paid -> should show up.
        Order.objects.create(
            member=self.alice,
            coffee_sort=self.espresso,
            value=Decimal("10.00"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=40),
        )
        # Bob: overspent this month only -> should NOT show up (pillory ignores current month).
        Order.objects.create(member=self.bob, coffee_sort=self.jug, value=Decimal("10.00"), machine_strokes=2)

        entries = services.deposit_pillory()
        uids = [e["member"].uid for e in entries]
        self.assertIn(self.alice.uid, uids)
        self.assertNotIn(self.bob.uid, uids)

    def test_deposit_pillory_ignores_positive_balances(self):
        Payment.objects.create(member=self.alice, value=Decimal("100.00"))
        entries = services.deposit_pillory()
        self.assertEqual(entries, [])

    def test_deposit_pillory_excludes_departed_members(self):
        Order.objects.create(
            member=self.alice,
            coffee_sort=self.espresso,
            value=Decimal("10.00"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=40),
        )
        self.alice.departed_at = timezone.now()
        self.alice.save()

        entries = services.deposit_pillory()
        self.assertEqual(entries, [])

    def test_ranking_all_time_orders_by_strokes_desc(self):
        Order.objects.create(member=self.bob, coffee_sort=self.jug, value=Decimal("0.60"), machine_strokes=5)
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        ranking = list(services.ranking_all_time())
        self.assertEqual(ranking[0].uid, self.bob.uid)

    def test_ranking_all_time_excludes_departed_members(self):
        Order.objects.create(member=self.bob, coffee_sort=self.jug, value=Decimal("0.60"), machine_strokes=5)
        self.bob.departed_at = timezone.now()
        self.bob.save()

        ranking = [m.uid for m in services.ranking_all_time()]
        self.assertNotIn(self.bob.uid, ranking)

    def test_coffee_distribution_counts_orders_per_sort(self):
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        Order.objects.create(member=self.bob, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        Order.objects.create(member=self.bob, coffee_sort=self.jug, value=Decimal("0.60"), machine_strokes=2)
        dist = services.coffee_distribution()
        counts = dict(zip(dist["labels"], dist["counts"]))
        self.assertEqual(counts["espresso"], 2)
        self.assertEqual(counts["jug"], 1)

    def test_monthly_totals_covers_requested_number_of_months(self):
        totals = services.monthly_totals(months=6)
        self.assertEqual(len(totals["labels"]), 6)
        self.assertEqual(len(totals["orders"]), 6)
        self.assertEqual(len(totals["payments"]), 6)

    def test_payment_method_totals_groups_by_method_name(self):
        Payment.objects.create(member=self.alice, value=Decimal("5.00"), method_name="Cash")
        Payment.objects.create(member=self.bob, value=Decimal("3.00"), method_name="Cash")
        Payment.objects.create(member=self.alice, value=Decimal("10.00"), method_name="PayPal")
        totals = services.payment_method_totals()
        by_name = dict(zip(totals["labels"], totals["totals"]))
        self.assertEqual(by_name["Cash"], 8.0)
        self.assertEqual(by_name["PayPal"], 10.0)


class PaymentMethodModelTestCase(TestCase):
    def test_custom_provider_substitutes_amount_placeholder(self):
        method = PaymentMethod(name="Custom", link_template="https://paypal.me/club/{amount}EUR")
        self.assertEqual(method.build_link(Decimal("12.5")), "https://paypal.me/club/12.50EUR")

    def test_custom_provider_without_placeholder_is_returned_as_is(self):
        method = PaymentMethod(name="Pool", link_template="https://paypal.com/pools/c/abc")
        self.assertEqual(method.build_link(Decimal("12.5")), "https://paypal.com/pools/c/abc")

    def test_paypal_me_preset_only_needs_a_username(self):
        method = PaymentMethod(name="PayPal", provider=PaymentMethod.PROVIDER_PAYPAL_ME, identifier="@coffeeclub")
        self.assertEqual(method.build_link(Decimal("12.5")), "https://paypal.me/coffeeclub/12.50EUR")

    def test_paypal_me_preset_without_username_has_no_link(self):
        method = PaymentMethod(name="PayPal", provider=PaymentMethod.PROVIDER_PAYPAL_ME, identifier="")
        self.assertEqual(method.build_link(Decimal("12.5")), "")

    def test_paypal_pool_preset_builds_link_from_a_bare_id(self):
        method = PaymentMethod(name="Pool", provider=PaymentMethod.PROVIDER_PAYPAL_POOL, identifier="abc123")
        self.assertEqual(method.build_link(Decimal("12.5")), "https://www.paypal.com/pools/c/abc123")

    def test_paypal_pool_preset_accepts_a_full_url_as_is(self):
        method = PaymentMethod(
            name="Pool", provider=PaymentMethod.PROVIDER_PAYPAL_POOL, identifier="https://paypal.com/pools/c/xyz"
        )
        self.assertEqual(method.build_link(Decimal("12.5")), "https://paypal.com/pools/c/xyz")

    def test_wero_preset_has_no_link_but_gives_instructions(self):
        method = PaymentMethod(name="WERO", provider=PaymentMethod.PROVIDER_WERO, identifier="+49 151 12345678")
        self.assertEqual(method.build_link(Decimal("12.5")), "")
        self.assertEqual(method.instructions(Decimal("12.5")), "Send 12.50€ via WERO to +49 151 12345678")
