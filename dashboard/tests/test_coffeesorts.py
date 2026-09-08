from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort


class CoffeeSortCrudTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:coffeesorts"))
        self.assertEqual(response.status_code, 302)

    def test_list_shows_existing_sorts(self):
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        response = self.client.get(reverse("dashboard:coffeesorts"))
        self.assertContains(response, "espresso")
        self.assertContains(response, "0.30")

    def test_add_creates_a_sort(self):
        response = self.client.post(
            reverse("dashboard:coffeesort_add"),
            {"name": "cappuccino", "price": "0.45", "machine_strokes": "2"},
        )
        self.assertRedirects(response, reverse("dashboard:coffeesorts"))
        sort = CoffeeSort.objects.get(name="cappuccino")
        self.assertEqual(sort.machine_strokes, 2)

    def test_add_rejects_duplicate_name(self):
        CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        response = self.client.post(
            reverse("dashboard:coffeesort_add"),
            {"name": "espresso", "price": "0.50", "machine_strokes": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CoffeeSort.objects.filter(name="espresso").count(), 1)

    def test_edit_updates_price(self):
        sort = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        response = self.client.post(
            reverse("dashboard:coffeesort_edit", args=[sort.pk]),
            {"name": "espresso", "price": "0.35", "machine_strokes": "1"},
        )
        self.assertRedirects(response, reverse("dashboard:coffeesorts"))
        sort.refresh_from_db()
        self.assertEqual(sort.price, Decimal("0.35"))

    def test_delete_removes_the_sort(self):
        sort = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        response = self.client.post(reverse("dashboard:coffeesort_delete", args=[sort.pk]))
        self.assertRedirects(response, reverse("dashboard:coffeesorts"))
        self.assertFalse(CoffeeSort.objects.filter(pk=sort.pk).exists())

    def test_delete_requires_post(self):
        sort = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        response = self.client.get(reverse("dashboard:coffeesort_delete", args=[sort.pk]))
        self.assertEqual(response.status_code, 405)
