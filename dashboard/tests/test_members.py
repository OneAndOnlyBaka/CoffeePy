from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import AuditLogEntry, CoffeeSort, Member, MemberDevice, MemberLinkToken, Order


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
            {"uid": "42", "nickname": "alice b", "favourite_coffee": self.espresso.pk},
        )
        self.assertRedirects(response, reverse("dashboard:members"))
        member = Member.objects.get(uid=42)
        self.assertEqual(member.nickname, "Alice B.")  # normalized, like at the kiosk
        self.assertEqual(member.favourite_coffee, self.espresso)

    def test_edit_updates_nickname_but_not_uid(self):
        Member.objects.create(uid=42, nickname="Alice")
        response = self.client.post(
            reverse("dashboard:member_edit", args=[42]),
            {"uid": "999", "nickname": "Alicia B.", "favourite_coffee": ""},
        )
        self.assertRedirects(response, reverse("dashboard:members"))
        member = Member.objects.get(uid=42)
        self.assertEqual(member.nickname, "Alicia B.")
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


class MemberNameRulesTestCase(TestCase):
    """The dashboard applies coffee/names.py like the kiosk, shows who will be asked for a
    name, and keeps the extra blocked names."""

    def setUp(self):
        User.objects.create_superuser(username="boss", password="pw")
        self.client.login(username="boss", password="pw")

    def test_a_refused_name_is_a_form_error(self):
        Member.objects.create(uid=7, nickname="Tom K.")
        for nickname, reason in (("Tom", "the first letter of your last name"), ("tom k", "Somebody already goes by that name"),
                                 ("Test Y.", "doesn&#x27;t look like a first name")):
            with self.subTest(nickname=nickname):
                response = self.client.post(reverse("dashboard:member_add"), {"uid": "8", "nickname": nickname})
                self.assertContains(response, reason)
        self.assertFalse(Member.objects.filter(uid=8).exists())

    def test_the_list_marks_who_needs_a_new_name(self):
        Member.objects.create(uid=1, nickname="1")
        Member.objects.create(uid=2, nickname="Anna B.")
        response = self.client.get(reverse("dashboard:members"))
        self.assertContains(response, "New name at next coffee", count=1)

    def test_extra_blocked_names(self):
        Member.objects.create(uid=2, nickname="Chef B.")
        self.assertNotContains(self.client.get(reverse("dashboard:members")), "New name at next coffee")
        self.client.post(reverse("dashboard:member_name_rules"), {"blocked_names": "Chef\nBoss"})
        self.assertContains(self.client.get(reverse("dashboard:members")), "New name at next coffee", count=1)
        self.assertTrue(AuditLogEntry.objects.filter(action="name_policy.update", details="boss, chef").exists())


class LastCoffeeTestCase(TestCase):
    """The member list says when each member last had a coffee - how a nameless card that
    left the company is told from one still in use."""

    def test_last_coffee_per_member(self):
        User.objects.create_superuser(username="boss", password="pw")
        self.client.login(username="boss", password="pw")
        sort = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        anna = Member.objects.create(uid=1, nickname="Anna B.")
        Member.objects.create(uid=2, nickname="Ben K.")
        for days in (40, 3):
            Order.objects.create(member=anna, coffee_sort=sort, value=Decimal("0.30"), machine_strokes=1,
                                 created_at=timezone.now() - timedelta(days=days))
        MemberDevice.objects.create(member=anna, session_key="a", label="a")
        MemberDevice.objects.create(member=anna, session_key="b", label="b")
        response = self.client.get(reverse("dashboard:members"))
        newest = timezone.localtime(timezone.now() - timedelta(days=3)).strftime("%m/%d/%Y")
        self.assertContains(response, f"Last coffee {newest}")
        self.assertContains(response, "No coffee yet", count=1)
        self.assertContains(response, "2 phone(s)")  # not multiplied by the orders it is joined with
