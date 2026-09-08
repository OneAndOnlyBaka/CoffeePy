from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, NfcScan, Order, Payment


class MonthChangeReminderTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        self.client.get(reverse("kiosk:index"))

    def _scan(self):
        NfcScan.objects.create(uid="2A", event=NfcScan.EVENT_INSERTED)  # uid_as_int() -> 42

    def test_no_reminder_when_paid_up(self):
        self._scan()
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "Reminder:")

    def test_reminder_shown_for_balance_owed_before_this_month(self):
        Order.objects.create(
            member=self.alice,
            coffee_sort=self.espresso,
            value=Decimal("15.00"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=40),
        )
        self._scan()
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Reminder: you still owe 15.00€ from last month")

    def test_no_reminder_once_settled(self):
        Order.objects.create(
            member=self.alice,
            coffee_sort=self.espresso,
            value=Decimal("15.00"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=40),
        )
        Payment.objects.create(member=self.alice, value=Decimal("15.00"))
        self._scan()
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "Reminder:")

    def test_orders_this_month_dont_trigger_the_reminder(self):
        Order.objects.create(member=self.alice, coffee_sort=self.espresso, value=Decimal("15.00"), machine_strokes=1)
        self._scan()
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "Reminder:")
