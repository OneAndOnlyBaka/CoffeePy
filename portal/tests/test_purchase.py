from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee import services
from coffee.models import InventoryItem, Member, StockCount
from dashboard.models import InventoryPolicy


class PortalPurchaseTestCase(TestCase):
    def setUp(self):
        self.member = Member.objects.create(uid=1, nickname="Alice X.")
        self.coffee = InventoryItem.objects.create(name="Coffee beans", unit="kg")
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "members"})
        self.client = Client()
        session = self.client.session
        session["portal_member_uid"] = self.member.pk
        session.save()

    def test_switched_off_there_is_no_tab_and_no_page(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "off"})
        self.assertNotContains(self.client.get(reverse("portal:index")), reverse("portal:purchase"))
        self.assertEqual(self.client.get(reverse("portal:purchase")).status_code, 404)

    def test_kept_in_the_dashboard_members_book_nothing(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "dashboard"})
        self.assertNotContains(self.client.get(reverse("portal:index")), reverse("portal:purchase"))
        self.assertEqual(self.client.get(reverse("portal:purchase")).status_code, 404)

    def test_booking_a_purchase(self):
        self.assertContains(self.client.get(reverse("portal:index")), reverse("portal:purchase"))
        response = self.client.post(reverse("portal:purchase"),
                                    {"form": "purchase", "item": self.coffee.pk, "quantity": "1", "price": "4,99"})
        self.assertRedirects(response, reverse("portal:purchase") + "?booked=1")
        self.assertEqual(services.account_balance(self.member), Decimal("4.99"))
        self.assertContains(self.client.get(reverse("portal:index")), "Purchase &middot; Coffee beans")

    def test_a_purchase_needs_an_item(self):
        response = self.client.post(reverse("portal:purchase"), {"form": "purchase", "quantity": "1", "price": "4"})
        self.assertContains(response, "Pick an item")

    def test_stock_take_only_for_the_picked_members(self):
        self.assertNotContains(self.client.get(reverse("portal:purchase")), "Save stock-take")
        self.client.post(reverse("portal:purchase"), {"form": "stock", f"count-{self.coffee.pk}": "3"})
        self.assertFalse(StockCount.objects.exists())
        self.member.may_take_stock = True
        self.member.save()
        self.assertContains(self.client.get(reverse("portal:purchase")), "Save stock-take")
        self.client.post(reverse("portal:purchase"), {"form": "stock", f"count-{self.coffee.pk}": "3"})
        self.assertEqual(StockCount.objects.get().quantity, Decimal("3"))
