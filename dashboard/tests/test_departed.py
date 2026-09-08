from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry, Member


class MemberDepartedTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.alice = Member.objects.create(uid=42, nickname="Alice")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("dashboard:member_mark_departed", args=[42]))
        self.assertEqual(response.status_code, 302)

    def test_mark_departed_sets_timestamp_and_logs_audit(self):
        response = self.client.post(reverse("dashboard:member_mark_departed", args=[42]))
        self.assertRedirects(response, reverse("dashboard:members"))
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_departed)
        self.assertIsNotNone(self.alice.departed_at)
        self.assertTrue(AuditLogEntry.objects.filter(action="member.mark_departed", target="Alice").exists())

    def test_members_list_shows_departed_status(self):
        self.client.post(reverse("dashboard:member_mark_departed", args=[42]))
        response = self.client.get(reverse("dashboard:members"))
        self.assertContains(response, "Departed")

    def test_reactivate_clears_departed_status_and_logs_audit(self):
        self.client.post(reverse("dashboard:member_mark_departed", args=[42]))
        response = self.client.post(reverse("dashboard:member_reactivate", args=[42]))
        self.assertRedirects(response, reverse("dashboard:members"))
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.is_departed)
        self.assertIsNone(self.alice.departed_at)
        self.assertTrue(AuditLogEntry.objects.filter(action="member.reactivate", target="Alice").exists())

    def test_mark_departed_requires_post(self):
        response = self.client.get(reverse("dashboard:member_mark_departed", args=[42]))
        self.assertEqual(response.status_code, 405)
