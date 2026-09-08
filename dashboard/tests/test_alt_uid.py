from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import AltUidCapture, Member


class MemberAltUidCaptureTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.alice = Member.objects.create(uid=42, nickname="Alice")

    def test_start_arms_a_capture_and_clears_previous_ones(self):
        bob = Member.objects.create(uid=43, nickname="Bob")
        self.client.get(reverse("dashboard:member_alt_uid_start", args=[bob.uid]))
        self.assertEqual(AltUidCapture.objects.count(), 1)

        self.client.get(reverse("dashboard:member_alt_uid_start", args=[self.alice.uid]))
        self.assertEqual(AltUidCapture.objects.count(), 1)
        self.assertEqual(AltUidCapture.objects.get().member_id, self.alice.uid)

    def test_poll_waits_while_capture_still_armed(self):
        self.client.get(reverse("dashboard:member_alt_uid_start", args=[self.alice.uid]))
        response = self.client.get(reverse("dashboard:member_alt_uid_poll", args=[self.alice.uid]))
        self.assertContains(response, "Waiting for a card")
        self.assertNotIn("HX-Redirect", response.headers)

    def test_poll_redirects_once_kiosk_fulfills_and_deletes_the_capture(self):
        self.client.get(reverse("dashboard:member_alt_uid_start", args=[self.alice.uid]))
        AltUidCapture.objects.all().delete()  # simulates the kiosk applying it successfully

        response = self.client.get(reverse("dashboard:member_alt_uid_poll", args=[self.alice.uid]))
        self.assertEqual(response.headers["HX-Redirect"], reverse("dashboard:members"))

    def test_poll_reports_timeout_and_cleans_up_the_expired_capture(self):
        AltUidCapture.objects.create(member=self.alice, expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(reverse("dashboard:member_alt_uid_poll", args=[self.alice.uid]))
        self.assertContains(response, "Timed out")
        self.assertFalse(AltUidCapture.objects.exists())

    def test_poll_shows_a_conflict_error_recorded_by_the_kiosk(self):
        AltUidCapture.objects.create(
            member=self.alice,
            expires_at=timezone.now() + timedelta(seconds=60),
            last_error="That card is already linked to another account - please try a different card.",
        )
        response = self.client.get(reverse("dashboard:member_alt_uid_poll", args=[self.alice.uid]))
        self.assertContains(response, "already linked to another account")
        self.assertContains(response, "Waiting for a card")
