from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, Member, NfcScan, Order, Payment, PaymentMethod


class KioskPayTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice X.")
        self.client.get(reverse("kiosk:index"))
        self.client.get(reverse("kiosk:nav", args=["info"]))
        NfcScan.objects.create(uid="2A", event=NfcScan.EVENT_INSERTED)  # uid_as_int() -> 42
        self.client.get(reverse("kiosk:panel"))  # logs in

    def test_pay_defaults_to_cash(self):
        self.client.post(reverse("kiosk:pay"), {"amount": "5.00"})
        payment = Payment.objects.get(member=self.alice)
        self.assertEqual(payment.method_name, "Cash")

    def test_pay_accepts_a_decimal_comma(self):
        self.client.post(reverse("kiosk:pay"), {"amount": "1,50"})
        payment = Payment.objects.get(member=self.alice)
        self.assertEqual(payment.value, Decimal("1.50"))

    def test_pay_ignores_amounts_that_are_not_numbers(self):
        for amount in ("NaN", "Infinity", "abc"):
            response = self.client.post(reverse("kiosk:pay"), {"amount": amount})
            self.assertEqual(response.status_code, 200, amount)
        self.assertFalse(Payment.objects.exists())

    def test_pay_records_chosen_method(self):
        PaymentMethod.objects.create(name="PayPal", link_template="https://paypal.me/club/{amount}EUR")
        self.client.post(reverse("kiosk:pay"), {"amount": "5.00", "method": "PayPal"})
        payment = Payment.objects.get(member=self.alice)
        self.assertEqual(payment.method_name, "PayPal")

    def test_qr_row_only_shown_once_something_is_owed(self):
        PaymentMethod.objects.create(name="PayPal", link_template="https://paypal.me/club/{amount}EUR")
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "<h2>PayPal</h2>")

        Order.objects.create(member=self.alice, value=Decimal("15.00"), machine_strokes=1)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "<h2>PayPal</h2>")

    def test_qr_row_omits_methods_with_no_link(self):
        PaymentMethod.objects.create(name="WERO", link_template="")
        Order.objects.create(member=self.alice, value=Decimal("15.00"), machine_strokes=1)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "<h2>WERO</h2>")
        # still offered as a selectable method for the manual "paid via" record though
        self.assertContains(response, "WERO")

    def test_wero_preset_shows_instructions_instead_of_a_qr(self):
        PaymentMethod.objects.create(
            name="WERO", provider=PaymentMethod.PROVIDER_WERO, identifier="+49 151 12345678"
        )
        Order.objects.create(member=self.alice, value=Decimal("15.00"), machine_strokes=1)
        response = self.client.get(reverse("kiosk:panel"))
        self.assertNotContains(response, "<h2>WERO</h2>")
        self.assertContains(response, "Send 15.00€ via WERO to +49 151 12345678")
