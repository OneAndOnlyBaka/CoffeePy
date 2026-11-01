import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee import services
from coffee.models import AuditLogEntry, CoffeeSort, InventoryItem, Member, NfcReaderStatus, NfcScan, Purchase, StockCount
from dashboard.models import InventoryPolicy


def inventory(enabled):
    InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "members" if enabled else "off"})


class KioskPurchaseTestCase(TestCase):
    """Booking a purchase at the kiosk, and the stock-take for the members picked for it."""

    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.")
        self.coffee = InventoryItem.objects.create(name="Coffee beans", unit="kg")
        inventory(True)
        self.client.get(reverse("kiosk:index"))

    def nav(self, mode):
        return self.client.get(reverse("kiosk:nav", args=[mode]), HTTP_HX_REQUEST="true")

    def log_in(self):
        self.nav("purchase")
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel"))

    def test_switched_off_there_is_no_purchase_screen(self):
        inventory(False)
        index = self.client.get(reverse("kiosk:index")).content.decode()
        self.assertRegex(index, r'data-mode="purchase"[^>]*\shidden>')
        self.assertContains(self.nav("purchase"), 'data-mode="order"')
        self.assertContains(self.client.get(reverse("kiosk:panel")), 'data-inventory="0"')

    def test_kept_in_the_dashboard_the_kiosk_has_no_purchase_screen(self):
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "dashboard"})
        index = self.client.get(reverse("kiosk:index")).content.decode()
        self.assertRegex(index, r'data-mode="purchase"[^>]*\shidden>')
        self.assertContains(self.nav("purchase"), 'data-inventory="0"')

    def test_no_booking_once_members_may_not(self):
        self.log_in()
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "dashboard"})
        self.client.post(reverse("kiosk:purchase_book"), {"item_id": self.coffee.pk, "quantity": "1", "price": "5"})
        self.assertFalse(Purchase.objects.exists())

    def test_switched_off_while_logged_in(self):
        self.log_in()
        inventory(False)
        panel = self.client.get(reverse("kiosk:panel"))
        self.assertContains(panel, 'data-mode="order"')
        self.assertFalse(self.client.session["logged_in"])

    def test_the_wait_screen_says_what_a_tap_does(self):
        panel = self.nav("purchase")
        self.assertContains(panel, "to book what you bought")
        self.assertContains(panel, "wait_purchase.gif")

    def test_a_new_card_gives_its_name_and_goes_on_to_the_purchase(self):
        """Seen on the device: a new card jumped to the settings without a word, and after saving
        the name the purchase was gone."""
        self.nav("purchase")
        NfcScan.objects.create(uid="11223344", event=NfcScan.EVENT_INSERTED)
        panel = self.client.get(reverse("kiosk:panel"))
        self.assertContains(panel, "Welcome, new card!")
        self.assertContains(panel, "then on to your purchase")
        panel = self.client.post(reverse("kiosk:settings_save"), {"nickname": "Bea Y."})
        self.assertContains(panel, "Coffee beans")
        self.assertEqual(self.client.session["mode"], "purchase")

    def test_a_name_that_fails_the_rules_is_asked_for_first(self):
        Member.objects.filter(pk=self.member.pk).update(nickname="Tom")
        panel = self.log_in()
        self.assertContains(panel, "Before your purchase")
        self.assertNotContains(panel, "purchase-card")
        panel = self.client.post(reverse("kiosk:settings_save"), {"nickname": "Tom X."})
        self.assertContains(panel, "Coffee beans")

    def test_booking_a_purchase_credits_the_member(self):
        panel = self.log_in()
        self.assertContains(panel, "Coffee beans")
        self.assertNotContains(panel, "Stock-take")  # not one of the picked ones
        panel = self.client.post(reverse("kiosk:purchase_book"),
                                 {"item_id": self.coffee.pk, "quantity": "1", "price": "14,99"})
        self.assertContains(panel, "14.99€ credited to you")
        self.assertEqual(services.account_balance(self.member), Decimal("14.99"))
        entry = AuditLogEntry.objects.get(action="purchase.create")
        self.assertIn("Tom X.", entry.actor)
        self.assertIn("1 kg Coffee beans, 14.99 EUR", entry.details)

    def test_a_purchase_needs_an_item_quantity_and_price(self):
        self.log_in()
        for data in ({"quantity": "1", "price": "2"}, {"item_id": self.coffee.pk, "quantity": "0", "price": "2"},
                     {"item_id": self.coffee.pk, "quantity": "1", "price": "NaN"}):
            with self.subTest(data):
                self.assertContains(self.client.post(reverse("kiosk:purchase_book"), data), "Pick an item")
        self.assertFalse(Purchase.objects.exists())

    def test_only_the_picked_members_take_stock(self):
        self.log_in()
        self.client.post(reverse("kiosk:stock_count"), {"count_item_id": self.coffee.pk, "count": "3"})
        self.assertFalse(StockCount.objects.exists())
        self.nav("order")
        self.member.may_take_stock = True
        self.member.save()
        panel = self.log_in()
        self.assertContains(panel, "Stock-take")
        self.assertContains(panel, '<span class="price">0 kg</span>')
        panel = self.client.post(reverse("kiosk:stock_count"), {"count_item_id": self.coffee.pk, "count": "2,5"})
        self.assertContains(panel, "Saved: Coffee beans 2.5 kg.")
        self.assertContains(panel, 'id="section-stock"\n                    checked')
        self.assertEqual(StockCount.objects.get().counted_by, "Tom X.")
        self.assertTrue(AuditLogEntry.objects.filter(action="stock.count", details="Coffee beans 2.5").exists())

    def test_the_screen_is_identical_from_poll_to_poll(self):
        self.member.may_take_stock = True
        self.member.save()
        first = self.log_in().content
        self.assertIn(b"data-deadline=", first)
        self.assertNotIn(b"csrfmiddlewaretoken", first)
        with mock.patch("time.time", return_value=time.time() + 3):
            self.assertEqual(first, self.client.get(reverse("kiosk:panel")).content)

    def test_many_items_come_in_pages(self):
        for number in range(10):
            InventoryItem.objects.create(name=f"Item {number:02}")
        panel = self.log_in().content.decode()
        self.assertEqual(panel.count("data-page="), 2)
        self.assertIn('data-sort-page="1"', panel)
