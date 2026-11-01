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
            {"name": "Link", "provider": "custom", "link_template": "https://example.com/pay", "is_active": "on", "order": "0"},
        )
        self.assertRedirects(response, reverse("dashboard:paymentmethods"))
        self.assertTrue(PaymentMethod.objects.filter(name="Link").exists())

    def test_wero_is_gone(self):
        response = self.client.post(reverse("dashboard:paymentmethod_add"),
                                    {"name": "WERO", "provider": "wero", "identifier": "+49 151 12345678", "order": "0"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PaymentMethod.objects.exists())

    def test_girocode_needs_recipient_and_a_valid_iban(self):
        url = reverse("dashboard:paymentmethod_add")
        data = {"name": "Bank", "provider": "girocode", "recipient": "Kaffeekasse", "order": "0", "is_active": "on"}
        response = self.client.post(url, {**data, "iban": "DE02 1203 0000 0000 2020 52"})
        self.assertContains(response, "not a valid IBAN")
        response = self.client.post(url, {**data, "recipient": "", "iban": "DE02120300000000202051"})
        self.assertContains(response, "This provider needs it.")
        response = self.client.post(url, {**data, "iban": "de02 1203 0000 0000 2020 51", "bic": "bylademm",
                                          "identifier": "left over from PayPal", "link_template": "https://x"})
        self.assertRedirects(response, reverse("dashboard:paymentmethods"))
        method = PaymentMethod.objects.get(name="Bank")
        self.assertEqual((method.iban, method.bic, method.identifier, method.link_template),
                         ("DE02120300000000202051", "BYLADEMM", "", ""))

    def test_paypal_needs_its_identifier(self):
        response = self.client.post(reverse("dashboard:paymentmethod_add"),
                                    {"name": "PayPal", "provider": "paypal_me", "identifier": "", "order": "0"})
        self.assertContains(response, "This provider needs it.")

    def test_the_form_marks_each_field_with_its_providers(self):
        page = self.client.get(reverse("dashboard:paymentmethod_add")).content.decode()
        self.assertIn('data-providers="paypal_me paypal_pool"', page)
        self.assertIn('data-providers="girocode"', page)
        self.assertIn('data-providers="custom"', page)

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
