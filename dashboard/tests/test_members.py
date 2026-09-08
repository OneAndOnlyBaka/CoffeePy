from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, MemberLinkToken


class MemberCrudTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:members"))
        self.assertEqual(response.status_code, 302)

    def test_list_shows_existing_members(self):
        Member.objects.create(uid=42, nickname="Alice", favourite_coffee=self.espresso)
        response = self.client.get(reverse("dashboard:members"))
        self.assertContains(response, "Alice")
        self.assertContains(response, "espresso")

    def test_add_creates_a_member(self):
        response = self.client.post(
            reverse("dashboard:member_add"),
            {"uid": "42", "nickname": "Alice", "favourite_coffee": self.espresso.pk},
        )
        self.assertRedirects(response, reverse("dashboard:members"))
        member = Member.objects.get(uid=42)
        self.assertEqual(member.nickname, "Alice")
        self.assertEqual(member.favourite_coffee, self.espresso)

    def test_edit_updates_nickname_but_not_uid(self):
        Member.objects.create(uid=42, nickname="Alice")
        response = self.client.post(
            reverse("dashboard:member_edit", args=[42]),
            {"uid": "999", "nickname": "Alicia", "favourite_coffee": ""},
        )
        self.assertRedirects(response, reverse("dashboard:members"))
        member = Member.objects.get(uid=42)
        self.assertEqual(member.nickname, "Alicia")
        self.assertFalse(Member.objects.filter(uid=999).exists())

    def test_delete_removes_the_member(self):
        Member.objects.create(uid=42, nickname="Alice")
        response = self.client.post(reverse("dashboard:member_delete", args=[42]))
        self.assertRedirects(response, reverse("dashboard:members"))
        self.assertFalse(Member.objects.filter(uid=42).exists())

    def test_revoke_devices_logs_out_all_of_a_members_linked_phones(self):
        alice = Member.objects.create(uid=42, nickname="Alice")
        portal_client = Client()
        token = MemberLinkToken.objects.create(member=alice, expires_at=timezone.now() + timedelta(minutes=5))
        portal_client.get(reverse("portal:link", args=[token.token]))
        session_key = portal_client.session.session_key

        response = self.client.post(reverse("dashboard:member_revoke_devices", args=[alice.uid]))
        self.assertRedirects(response, reverse("dashboard:members"))
        self.assertFalse(alice.devices.exists())
        self.assertFalse(Session.objects.filter(pk=session_key).exists())

        response = portal_client.get(reverse("portal:index"))
        self.assertRedirects(response, reverse("portal:link_invalid"))
