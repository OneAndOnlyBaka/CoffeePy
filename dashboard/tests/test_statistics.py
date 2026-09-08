import json
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, Order


class StatisticsViewTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        sort = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        member = Member.objects.create(uid=1, nickname="Alice")
        Order.objects.create(
            member=member,
            coffee_sort=sort,
            value=sort.price,
            machine_strokes=1,
            created_at=timezone.make_aware(datetime.fromisoformat("2026-01-05 09:15:00")),
        )

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("dashboard:statistics")).status_code, 302)

    def test_page_renders_the_time_of_day_charts(self):
        response = self.client.get(reverse("dashboard:statistics"))

        self.assertContains(response, 'id="hourlyChart"')
        self.assertContains(response, 'id="weekdayChart"')

    def test_context_carries_valid_json_for_the_charts(self):
        response = self.client.get(reverse("dashboard:statistics"))

        hourly = json.loads(response.context["hourly_distribution"])
        weekday = json.loads(response.context["weekday_distribution"])
        self.assertEqual(hourly["cups"][9], 1)
        self.assertEqual(weekday["cups"][0], 1)  # 2026-01-05 was a Monday
