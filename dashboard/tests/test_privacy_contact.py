"""dashboard → System → Privacy notice: who is responsible, as the kiosk's notice names it."""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry, Member
from dashboard.models import PrivacyContact


class PrivacyContactTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(User.objects.create_superuser("admin", password="x"))

    def test_saved_and_audited(self):
        self.client.post(reverse("dashboard:system_dashboard"), {"form": "privacy", "controller": "Anna B., room 2.14"})
        self.assertEqual(PrivacyContact.current().controller, "Anna B., room 2.14")
        self.assertTrue(AuditLogEntry.objects.filter(action="privacy_contact.update").exists())

    def test_the_members_page_shows_who_hasnt_confirmed(self):
        Member.objects.create(uid=1, nickname="Tom X.")
        self.assertContains(self.client.get(reverse("dashboard:members")), "Privacy notice not confirmed")
