from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee import services
from coffee.models import (
    AuditLogEntry,
    CoffeeSort,
    InventoryItem,
    Member,
    Order,
    Payment,
    PaymentMethod,
    Purchase,
    StockCount,
)
from dashboard.models import InventoryPolicy


class DashboardInventoryTestCase(TestCase):
    """The inventory pages are for admins and managers alike, and only while switched on."""

    def setUp(self):
        self.manager = User.objects.create_user("manager", password="pw")
        self.client = Client()
        self.client.force_login(self.manager)
        self.member = Member.objects.create(uid=1, nickname="Alice X.")
        self.coffee = InventoryItem.objects.create(name="Coffee beans", unit="kg")
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "members"})

    def test_switched_off_the_pages_are_gone(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "off"})
        self.assertNotContains(self.client.get(reverse("dashboard:index")), reverse("dashboard:inventory"))
        self.assertEqual(self.client.get(reverse("dashboard:inventory")).status_code, 404)
        self.assertNotIn("may_take_stock", self.client.get(reverse("dashboard:member_edit", args=[1])).context["form"].fields)

    def test_the_admin_switches_it_on(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "off"})
        User.objects.create_superuser("admin", password="pw")
        self.client.force_login(User.objects.get(username="admin"))
        for mode in ("dashboard", "members", "off"):
            self.client.post(reverse("dashboard:system_dashboard"), {"form": "inventory", "mode": mode})
            self.assertEqual(InventoryPolicy.current().mode, mode)
            self.assertTrue(AuditLogEntry.objects.filter(action="inventory_policy.update", details=mode).exists())
        self.client.post(reverse("dashboard:system_dashboard"), {"form": "inventory", "mode": "dashboard"})
        self.assertContains(self.client.get(reverse("dashboard:index")), reverse("dashboard:inventory"))

    def test_kept_in_the_dashboard_members_have_no_stock_take(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "dashboard"})
        self.assertEqual(self.client.get(reverse("dashboard:inventory")).status_code, 200)
        self.assertNotIn("may_take_stock", self.client.get(reverse("dashboard:member_edit", args=[1])).context["form"].fields)
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "members"})
        self.assertIn("may_take_stock", self.client.get(reverse("dashboard:member_edit", args=[1])).context["form"].fields)

    def test_a_purchase_from_the_coffee_box_credits_nobody(self):
        response = self.client.post(reverse("dashboard:inventory_purchase_add"), {
            "member": "", "item": self.coffee.pk, "quantity": "1", "price": "15,00", "created_at": "2026-10-01T09:00"})
        self.assertRedirects(response, reverse("dashboard:inventory_purchases"))
        purchase = Purchase.objects.get()
        self.assertIsNone(purchase.member)
        self.assertFalse(Payment.objects.exists())
        entry = AuditLogEntry.objects.get(action="purchase.create")
        self.assertEqual(entry.target, "coffee box")
        self.assertIn("(coffee box)", entry.details)
        self.assertContains(self.client.get(reverse("dashboard:inventory_purchases") + "?member=box"), "Coffee box")

        # to a member: the credit is made; back to the box: it goes
        data = {"item": self.coffee.pk, "quantity": "1", "price": "15,00", "created_at": "2026-10-01T09:00"}
        self.client.post(reverse("dashboard:inventory_purchase_edit", args=[purchase.pk]), {**data, "member": 1})
        self.assertEqual(services.account_balance(self.member), Decimal("15.00"))
        self.client.post(reverse("dashboard:inventory_purchase_edit", args=[purchase.pk]), {**data, "member": ""})
        self.assertEqual(services.account_balance(self.member), Decimal("0"))
        self.assertFalse(Payment.objects.exists())
        self.assertTrue(Purchase.objects.filter(pk=purchase.pk).exists())
        self.client.post(reverse("dashboard:inventory_purchase_delete", args=[purchase.pk]))
        self.assertFalse(Purchase.objects.exists())

    def test_a_manager_enters_corrects_and_deletes_a_purchase(self):
        self.client.post(reverse("dashboard:inventory_purchase_add"), {
            "member": 1, "item": self.coffee.pk, "quantity": "2", "price": "28,00", "created_at": "2026-10-01T09:00"})
        purchase = Purchase.objects.get()
        self.assertEqual(services.account_balance(self.member), Decimal("28.00"))
        # Bookings sends the credit's corrections to the purchase - the two must not drift apart.
        self.assertRedirects(self.client.get(reverse("dashboard:payment_edit", args=[purchase.payment_id])),
                             reverse("dashboard:inventory_purchase_edit", args=[purchase.pk]))
        self.client.post(reverse("dashboard:inventory_purchase_edit", args=[purchase.pk]), {
            "member": 1, "item": self.coffee.pk, "quantity": "2", "price": "26,00", "created_at": "2026-10-01T09:00"})
        self.assertEqual(Payment.objects.get().value, Decimal("26.00"))
        self.client.post(reverse("dashboard:inventory_purchase_delete", args=[purchase.pk]))
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(list(AuditLogEntry.objects.filter(action__startswith="purchase.").values_list("action", flat=True)
                              .order_by("id")), ["purchase.create", "purchase.update", "purchase.delete"])

    def test_stock_take(self):
        self.client.post(reverse("dashboard:inventory"), {f"count-{self.coffee.pk}": "1,5"})
        count = StockCount.objects.get()
        self.assertEqual((count.quantity, count.counted_by), (Decimal("1.5"), "manager"))
        self.assertContains(self.client.get(reverse("dashboard:inventory")), "1.5 kg")

    def test_an_item_that_was_bought_is_not_deleted(self):
        services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        self.client.post(reverse("dashboard:inventory_item_delete", args=[self.coffee.pk]))
        self.assertTrue(InventoryItem.objects.exists())
        self.assertTrue(AuditLogEntry.objects.filter(action="inventory_item.delete", outcome="failure").exists())

    def test_spending_page(self):
        services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("14.99"))
        self.assertContains(self.client.get(reverse("dashboard:inventory_spending")), "14.99€")

    def test_purchase_is_a_reserved_payment_method_name(self):
        response = self.client.post(reverse("dashboard:paymentmethod_add"),
                                    {"name": "purchase", "provider": "custom", "order": 0, "is_active": "on"})
        self.assertContains(response, "This name is reserved")
        self.assertFalse(PaymentMethod.objects.exists())

    def test_member_merge_keeps_the_purchases(self):
        other = Member.objects.create(uid=2, nickname="Bob Y.")
        services.book_purchase(other, self.coffee, Decimal("1"), Decimal("14.99"))
        self.client.post(reverse("dashboard:member_merge_confirm", args=[1, 2]))
        self.assertEqual(Purchase.objects.get().member, self.member)
        self.assertEqual(services.account_balance(self.member), Decimal("14.99"))

    def test_the_costs_page_suggests_nothing_without_data_and_saves_the_margin(self):
        page = self.client.get(reverse("dashboard:inventory_costs"))
        self.assertContains(page, "Not enough data yet")
        response = self.client.post(reverse("dashboard:inventory_costs") + "?months=6", {"price_margin_percent": "25"})
        self.assertRedirects(response, reverse("dashboard:inventory_costs") + "?months=6")
        self.assertEqual(InventoryPolicy.current().price_margin_percent, 25)
        self.assertTrue(AuditLogEntry.objects.filter(action="inventory_policy.update", details="price margin 25%").exists())
        self.assertEqual(self.client.post(reverse("dashboard:inventory_costs"), {"price_margin_percent": "101"}).status_code, 200)
        self.assertEqual(InventoryPolicy.current().price_margin_percent, 25)

    def test_the_costs_page_shows_the_suggestion(self):
        sort = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        now = timezone.now()
        Order.objects.bulk_create(Order(member=self.member, coffee_sort=sort, value=sort.price, machine_strokes=1)
                                  for _ in range(120))
        for days in (100, 70, 40):
            services.book_purchase(self.member, self.coffee, Decimal("1"), Decimal("12.00"),
                                   created_at=now - timedelta(days=days))
        page = self.client.get(reverse("dashboard:inventory_costs"))
        self.assertContains(page, "30.0 ct")
        self.assertContains(page, "0.35€")  # 0.30 + 10%, rounded up
        self.assertContains(page, reverse("dashboard:coffeesort_edit", args=[sort.pk]))

    def test_switched_off_there_are_no_costs(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "off"})
        self.assertEqual(self.client.get(reverse("dashboard:inventory_costs")).status_code, 404)
