from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import PaymentMethod


class PaymentMethodCrudTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:paymentmethods"))
        self.assertEqual(response.status_code, 302)

    def test_list_shows_existing_methods(self):
        PaymentMethod.objects.create(name="PayPal", link_template="https://paypal.me/club/{amount}EUR")
        response = self.client.get(reverse("dashboard:paymentmethods"))
        self.assertContains(response, "PayPal")

    def test_add_creates_a_method(self):
        response = self.client.post(
            reverse("dashboard:paymentmethod_add"),
            {"name": "WERO", "provider": "wero", "identifier": "+49 151 12345678", "link_template": "", "is_active": "on", "order": "0"},
        )
        self.assertRedirects(response, reverse("dashboard:paymentmethods"))
        self.assertTrue(PaymentMethod.objects.filter(name="WERO").exists())

    def test_add_paypal_me_preset_only_needs_a_username(self):
        response = self.client.post(
            reverse("dashboard:paymentmethod_add"),
            {"name": "PayPal", "provider": "paypal_me", "identifier": "coffeeclub", "link_template": "", "is_active": "on", "order": "0"},
        )
        self.assertRedirects(response, reverse("dashboard:paymentmethods"))
        method = PaymentMethod.objects.get(name="PayPal")
        self.assertEqual(method.build_link(Decimal("5.00")), "https://paypal.me/coffeeclub/5.00EUR")

    def test_add_rejects_duplicate_name(self):
        PaymentMethod.objects.create(name="PayPal")
        response = self.client.post(
            reverse("dashboard:paymentmethod_add"),
            {"name": "PayPal", "provider": "custom", "link_template": "", "is_active": "on", "order": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PaymentMethod.objects.filter(name="PayPal").count(), 1)

    def test_edit_updates_link_template(self):
        method = PaymentMethod.objects.create(name="PayPal")
        response = self.client.post(
            reverse("dashboard:paymentmethod_edit", args=[method.pk]),
            {
                "name": "PayPal",
                "provider": "custom",
                "link_template": "https://paypal.me/club/{amount}EUR",
                "is_active": "on",
                "order": "0",
            },
        )
        self.assertRedirects(response, reverse("dashboard:paymentmethods"))
        method.refresh_from_db()
        self.assertEqual(method.link_template, "https://paypal.me/club/{amount}EUR")

    def test_delete_removes_the_method(self):
        method = PaymentMethod.objects.create(name="PayPal")
        response = self.client.post(reverse("dashboard:paymentmethod_delete", args=[method.pk]))
        self.assertRedirects(response, reverse("dashboard:paymentmethods"))
        self.assertFalse(PaymentMethod.objects.filter(pk=method.pk).exists())
