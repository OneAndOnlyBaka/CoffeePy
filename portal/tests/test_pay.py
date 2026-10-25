from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import Member, MemberLinkToken, Order, Payment, PaymentMethod


class PortalPayTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        token = MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(minutes=5))
        self.client.get(reverse("portal:link", args=[token.token]))

    def test_cash_payment_is_always_recorded(self):
        response = self.client.post(reverse("portal:pay"), {"amount": "5.00", "method": "Cash"})
        self.assertRedirects(response, reverse("portal:index"))
        payment = Payment.objects.get(member=self.alice)
        self.assertEqual(payment.value, Decimal("5.00"))
        self.assertEqual(payment.method_name, "Cash")

    def test_configured_method_is_recorded_by_name(self):
        PaymentMethod.objects.create(name="PayPal", link_template="https://paypal.me/club/{amount}EUR")
        response = self.client.post(reverse("portal:pay"), {"amount": "3.50", "method": "PayPal"})
        self.assertRedirects(response, reverse("portal:index"))
        payment = Payment.objects.get(member=self.alice)
        self.assertEqual(payment.method_name, "PayPal")

    def test_direct_pay_link_only_shown_once_something_is_owed(self):
        PaymentMethod.objects.create(name="PayPal", link_template="https://paypal.me/club/{amount}EUR")
        Payment.objects.create(member=self.alice, value=Decimal("10.00"))  # in credit, nothing owed

        response = self.client.get(reverse("portal:pay"))
        self.assertNotContains(response, "paypal.me")

        Order.objects.create(member=self.alice, value=Decimal("25.00"), machine_strokes=1)  # now owes 15
        response = self.client.get(reverse("portal:pay"))
        self.assertContains(response, "paypal.me/club/15.00EUR")

    def test_inactive_method_is_not_offered(self):
        PaymentMethod.objects.create(name="WERO", link_template="", is_active=False)
        response = self.client.get(reverse("portal:pay"))
        self.assertNotContains(response, "WERO")

    def test_zero_or_negative_amount_is_not_recorded(self):
        self.client.post(reverse("portal:pay"), {"amount": "0", "method": "Cash"})
        self.assertFalse(Payment.objects.exists())

    def test_paypal_me_preset_only_needs_a_username(self):
        PaymentMethod.objects.create(name="PayPal", provider=PaymentMethod.PROVIDER_PAYPAL_ME, identifier="coffeeclub")
        Order.objects.create(member=self.alice, value=Decimal("15.00"), machine_strokes=1)
        response = self.client.get(reverse("portal:pay"))
        self.assertContains(response, "https://paypal.me/coffeeclub/15.00EUR")

    def test_wero_preset_shows_instructions_instead_of_a_link(self):
        PaymentMethod.objects.create(name="WERO", provider=PaymentMethod.PROVIDER_WERO, identifier="+49 151 12345678")
        Order.objects.create(member=self.alice, value=Decimal("15.00"), machine_strokes=1)
        response = self.client.get(reverse("portal:pay"))
        self.assertContains(response, "Send 15.00€ via WERO to +49 151 12345678")

    def test_amount_input_keeps_a_dot_decimal_in_german(self):
        """German renders 15.00 as "15,00", which an <input type="number"> rejects - the
        field would come up empty instead of prefilled with what is owed."""
        Order.objects.create(member=self.alice, value=Decimal("15.00"), machine_strokes=1)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        response = self.client.get(reverse("portal:pay"))
        self.assertContains(response, 'name="amount" value="15.00"')
        # The displayed balance is localized, so German really was active.
        self.assertContains(response, "-15,00€")
