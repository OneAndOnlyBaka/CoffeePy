from datetime import timedelta
from decimal import Decimal

from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import Member, MemberDevice, MemberLinkToken


class LinkBootstrapTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = Member.objects.create(uid=42, nickname="Alice")

    def _token(self, seconds=300):
        return MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(seconds=seconds))

    def test_valid_token_logs_in_and_creates_a_device(self):
        token = self._token()
        response = self.client.get(reverse("portal:link", args=[token.token]))
        self.assertRedirects(response, reverse("portal:index"))
        self.assertEqual(self.client.session["portal_member_uid"], self.alice.pk)
        self.assertTrue(MemberDevice.objects.filter(member=self.alice).exists())

    def test_token_is_single_use(self):
        token = self._token()
        self.client.get(reverse("portal:link", args=[token.token]))
        self.assertFalse(MemberLinkToken.objects.filter(pk=token.pk).exists())

        self.client.logout()
        response = self.client.get(reverse("portal:link", args=[token.token]))
        self.assertEqual(response.status_code, 410)

    def test_expired_token_is_rejected(self):
        token = self._token(seconds=-1)
        response = self.client.get(reverse("portal:link", args=[token.token]))
        self.assertEqual(response.status_code, 410)

    def test_unknown_token_is_rejected(self):
        response = self.client.get(reverse("portal:link", args=["not-a-real-token"]))
        self.assertEqual(response.status_code, 410)

    def test_index_requires_a_linked_session(self):
        response = self.client.get(reverse("portal:index"))
        self.assertRedirects(response, reverse("portal:link_invalid"))

    def test_session_rolls_forward_on_each_authenticated_request(self):
        token = self._token()
        self.client.get(reverse("portal:link", args=[token.token]))
        session = Session.objects.get(pk=self.client.session.session_key)
        first_expiry = session.expire_date

        self.client.get(reverse("portal:index"))
        session.refresh_from_db()
        self.assertGreaterEqual(session.expire_date, first_expiry)

    def test_revoked_session_bounces_to_link_invalid(self):
        token = self._token()
        self.client.get(reverse("portal:link", args=[token.token]))
        Session.objects.all().delete()

        response = self.client.get(reverse("portal:index"))
        self.assertRedirects(response, reverse("portal:link_invalid"))


class PortalIndexTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = Member.objects.create(uid=42, nickname="Alice")
        token = MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(minutes=5))
        self.client.get(reverse("portal:link", args=[token.token]))

    def test_shows_stats_and_balance(self):
        response = self.client.get(reverse("portal:index"))
        self.assertContains(response, "Alice")
        self.assertContains(response, "0.00")

    def test_logout_flushes_session_and_removes_device(self):
        response = self.client.post(reverse("portal:logout"))
        self.assertRedirects(response, reverse("portal:link_invalid"))
        self.assertFalse(MemberDevice.objects.filter(member=self.alice).exists())

        response = self.client.get(reverse("portal:index"))
        self.assertRedirects(response, reverse("portal:link_invalid"))

    def test_departed_member_is_kicked_out(self):
        self.alice.departed_at = timezone.now()
        self.alice.save()

        response = self.client.get(reverse("portal:index"))
        self.assertRedirects(response, reverse("portal:link_invalid"))
