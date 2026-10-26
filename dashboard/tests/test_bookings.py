from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee import services
from coffee.models import AuditLogEntry, CoffeeSort, Member, Order, Payment, PaymentMethod


class BookingsTestCase(TestCase):
    def setUp(self):
        # A manager, not an admin: corrections are everyday work.
        User.objects.create_user(username="helper", password="pw")
        self.client = Client()
        self.client.login(username="helper", password="pw")
        self.sort = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        self.anna = Member.objects.create(uid=1, nickname="Anna")
        self.ben = Member.objects.create(uid=2, nickname="Ben")
        self.order = Order.objects.create(member=self.anna, coffee_sort=self.sort, value=Decimal("0.30"),
                                          machine_strokes=1)
        Order.objects.create(member=self.ben, coffee_sort=self.sort, value=Decimal("0.30"), machine_strokes=1)

    def test_lists_orders_and_filters_by_member(self):
        response = self.client.get(reverse("dashboard:bookings"))
        self.assertContains(response, "Anna")
        self.assertContains(response, "Ben &middot;")
        response = self.client.get(reverse("dashboard:bookings"), {"member": self.anna.uid})
        self.assertNotContains(response, "Ben &middot;")

    def test_correct_an_order(self):
        when = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(reverse("dashboard:order_edit", args=[self.order.pk]), {
            "coffee_sort": self.sort.pk, "value": "0.60", "machine_strokes": "2", "created_at": when,
        })
        self.assertRedirects(response, f"{reverse('dashboard:bookings')}?kind=orders&member=1")
        self.order.refresh_from_db()
        self.assertEqual((self.order.value, self.order.machine_strokes), (Decimal("0.60"), 2))
        self.assertTrue(AuditLogEntry.objects.filter(action="order.update", target="Anna").exists())

    def test_delete_an_order(self):
        self.client.post(reverse("dashboard:order_delete", args=[self.order.pk]))
        self.assertFalse(Order.objects.filter(pk=self.order.pk).exists())
        self.assertTrue(AuditLogEntry.objects.filter(action="order.delete", target="Anna").exists())

    def test_enter_a_cash_payment(self):
        PaymentMethod.objects.create(name="PayPal", provider="paypal_me", identifier="coffeefund")
        form = self.client.get(reverse("dashboard:payment_add"), {"member": 1})
        self.assertContains(form, "PayPal")
        when = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(reverse("dashboard:payment_add"), {
            "member": 1, "value": "5,00", "method_name": "Cash", "created_at": when, "info": "handed over",
        })
        self.assertRedirects(response, f"{reverse('dashboard:bookings')}?kind=payments&member=1")
        payment = Payment.objects.get(member=self.anna)
        self.assertEqual((payment.value, payment.method_name), (Decimal("5.00"), "Cash"))
        self.assertTrue(AuditLogEntry.objects.filter(action="payment.create", target="Anna").exists())

    def test_a_payment_of_zero_is_refused(self):
        when = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(reverse("dashboard:payment_add"), {
            "member": 1, "value": "0", "method_name": "Cash", "created_at": when,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())

    def test_editing_keeps_a_method_that_no_longer_exists(self):
        payment = Payment.objects.create(member=self.anna, value=Decimal("3.00"), method_name="Old bank")
        response = self.client.get(reverse("dashboard:payment_edit", args=[payment.pk]))
        self.assertContains(response, 'value="Old bank" selected')

    def test_delete_a_payment(self):
        payment = Payment.objects.create(member=self.anna, value=Decimal("3.00"))
        self.client.post(reverse("dashboard:payment_delete", args=[payment.pk]))
        self.assertFalse(Payment.objects.exists())

    def test_a_correction_changes_what_the_kiosk_ranking_shows(self):
        # Neither max id moves when an older booking is corrected or deleted - the audit row
        # the dashboard writes is what tells the kiosk's cached ranking to recompute.
        before = services.ranking_fingerprint()
        self.client.post(reverse("dashboard:order_delete", args=[self.order.pk]))
        self.assertNotEqual(before, services.ranking_fingerprint())

    def test_old_orders_page_through(self):
        Order.objects.bulk_create(
            Order(member=self.anna, coffee_sort=self.sort, value=Decimal("0.30"), machine_strokes=1,
                  created_at=timezone.now() - timedelta(days=k))
            for k in range(1, 80)
        )
        response = self.client.get(reverse("dashboard:bookings"), {"page": 2})
        self.assertContains(response, "Page 2 of 2")
