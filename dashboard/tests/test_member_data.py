import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee import services
from coffee.models import AuditLogEntry, CoffeeSort, Member, MemberCard, MemberDevice, Order, Payment
from dashboard import audit
from dashboard.models import DeletionHold, MemberRetention


class MemberExportTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        sort = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=42, nickname="Alice X.", favourite_coffee=sort)
        self.bob = Member.objects.create(uid=420, nickname="Bob X.")
        MemberCard.objects.create(member=self.alice, uid=43)
        MemberDevice.objects.create(member=self.alice, session_key="s1", label="Firefox on Android")
        Order.objects.create(member=self.alice, coffee_sort=sort, value=Decimal("0.60"), machine_strokes=2)
        Order.objects.create(member=self.bob, coffee_sort=sort, value=Decimal("0.30"), machine_strokes=1)
        Payment.objects.create(member=self.alice, value=Decimal("1.00"))
        audit.log_actor(audit.member_actor(self.alice), "order.create", target=audit.member_actor(self.alice))
        audit.log_actor(audit.member_actor(self.bob), "order.create", target=audit.member_actor(self.bob))

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("dashboard:member_export", args=[42])).status_code, 302)

    def test_everything_about_one_member_and_nothing_about_another(self):
        response = self.client.get(reverse("dashboard:member_export", args=[42]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="coffeepy-member-42-', response["Content-Disposition"])
        data = json.loads(response.content)
        self.assertEqual(data["member"]["name"], "Alice X.")
        self.assertEqual(data["member"]["favourite_coffee"], "Espresso")
        self.assertEqual(data["member"]["balance"], "0.40")
        self.assertEqual(data["member"]["cups_total"], 2)
        self.assertEqual([card["uid"] for card in data["cards"]], [42, 43])
        self.assertEqual(data["phones"][0]["label"], "Firefox on Android")
        self.assertEqual([(o["cups"], o["value"]) for o in data["orders"]], [(2, "0.60")])
        self.assertEqual([p["value"] for p in data["payments"]], ["1.00"])
        # "(uid 42)" must not catch Bob's "(uid 420)"
        self.assertEqual([e["actor"] for e in data["audit_log"]], ["Alice X. (uid 42)"])

    def test_the_export_is_audited(self):
        self.client.get(reverse("dashboard:member_export", args=[42]))
        self.assertTrue(AuditLogEntry.objects.filter(action="member.export", target="Alice X. (uid 42)").exists())

    def test_offered_on_the_member_pages(self):
        url = reverse("dashboard:member_export", args=[42])
        self.assertContains(self.client.get(reverse("dashboard:member_edit", args=[42])), url)
        self.assertContains(self.client.get(reverse("dashboard:members")), url)


class MemberRetentionTestCase(TestCase):
    """Departed members are only ever suggested for deletion - the dashboard and the kiosk say so,
    nothing deletes them: the period is measured with a clock that can be wrong."""

    def setUp(self):
        self.client = Client()
        User.objects.create_superuser(username="admin", password="pw")
        self.client.login(username="admin", password="pw")
        self.sort = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)

    def departed(self, uid, days_ago, owes=Decimal("0")):
        member = Member.objects.create(uid=uid, nickname=f"M{uid} X.", departed_at=timezone.now() - timedelta(days=days_ago))
        for _ in range(3):  # 0.10 three times: a float sum would not come out at 0.30
            Order.objects.create(member=member, coffee_sort=self.sort, value=Decimal("0.10"), machine_strokes=1)
        Payment.objects.create(member=member, value=Decimal("0.30") - owes)
        return member

    def test_nothing_without_a_period(self):
        self.departed(1, 1000)
        self.assertEqual(services.departed_members_due(0), [])
        self.assertNotContains(self.client.get(reverse("dashboard:members")), "Suggested for deletion")

    def test_only_settled_members_past_the_period(self):
        self.departed(1, 200)                          # past, settled
        self.departed(2, 100)                          # not yet
        self.departed(3, 200, owes=Decimal("0.30"))    # past, but owes
        self.departed(4, 200, owes=Decimal("-1.00"))   # past, but has credit
        Member.objects.create(uid=5, nickname="Active X.")
        Member.objects.create(uid=6, nickname="Never X.", departed_at=timezone.now() - timedelta(days=300))  # no bookings
        self.assertEqual([m.uid for m in services.departed_members_due(6)], [6, 1])

    def test_never_deleted_by_itself(self):
        MemberRetention.objects.create(pk=1, departed_months=1)
        self.departed(1, 400)
        call_command("audit_housekeeping", stdout=mock.Mock())
        call_command("tick", stdout=mock.Mock(), stderr=mock.Mock())
        self.assertTrue(Member.objects.filter(uid=1).exists())

    def test_shown_on_members_page_and_overview(self):
        MemberRetention.objects.create(pk=1, departed_months=6)
        self.departed(1, 200)
        page = self.client.get(reverse("dashboard:members"))
        self.assertContains(page, "Suggested for deletion")
        self.assertContains(page, reverse("dashboard:member_delete", args=[1]))
        self.assertContains(self.client.get(reverse("dashboard:index")), "Departed members suggested for deletion: 1")
        self.client.post(reverse("dashboard:member_delete", args=[1]))
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "suggested for deletion")

    def test_a_task_sign_on_the_kiosk(self):
        MemberRetention.objects.create(pk=1, departed_months=6)
        kiosk = Client()
        self.assertNotContains(kiosk.get(reverse("kiosk:panel")), "admin-task")
        self.departed(1, 200)
        first = kiosk.get(reverse("kiosk:panel")).content
        self.assertIn(b"admin-task", first)
        self.assertEqual(first, kiosk.get(reverse("kiosk:panel")).content)  # the wait screen stays byte-identical

    def test_kept_with_a_reason(self):
        MemberRetention.objects.create(pk=1, departed_months=6)
        self.departed(1, 200)
        keep = reverse("dashboard:member_keep", args=[1])
        self.client.post(keep, {"reason": "  "})                       # no reason: refused
        self.assertEqual([m.uid for m in services.departed_members_due(6)], [1])
        self.client.post(keep, {"reason": "comes back in spring"})
        self.assertEqual(services.departed_members_due(6), [])
        hold = DeletionHold.objects.get(member_id=1)
        self.assertEqual((hold.reason, hold.held_by), ("comes back in spring", "admin"))
        self.assertTrue(AuditLogEntry.objects.filter(action="member.keep", details="comes back in spring").exists())
        page = self.client.get(reverse("dashboard:members"))
        self.assertContains(page, "comes back in spring")
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "suggested for deletion")
        self.assertNotIn(b"admin-task", Client().get(reverse("kiosk:panel")).content)
        self.client.post(reverse("dashboard:member_keep_undo", args=[1]))
        self.assertEqual([m.uid for m in services.departed_members_due(6)], [1])
        self.assertTrue(AuditLogEntry.objects.filter(action="member.keep_undo").exists())

    def test_only_departed_members_can_be_kept(self):
        Member.objects.create(uid=7, nickname="Active X.")
        response = self.client.post(reverse("dashboard:member_keep", args=[7]), {"reason": "x"})
        self.assertEqual(response.status_code, 404)

    def test_reactivating_drops_the_hold(self):
        member = self.departed(1, 200)
        DeletionHold.objects.create(member=member, reason="x")
        self.client.post(reverse("dashboard:member_reactivate", args=[1]))
        self.assertFalse(DeletionHold.objects.exists())

    def test_months_ago_keeps_to_the_calendar(self):
        with mock.patch.object(timezone, "now", return_value=datetime(2026, 3, 31, 12, tzinfo=dt_timezone.utc)):
            self.assertEqual(services.months_ago(1), datetime(2026, 2, 28, 12, tzinfo=dt_timezone.utc))
            self.assertEqual(services.months_ago(15), datetime(2024, 12, 31, 12, tzinfo=dt_timezone.utc))


class MemberRetentionViewTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser(username="admin", password="pw")
        self.client.login(username="admin", password="pw")

    def test_admin_sets_the_period(self):
        response = self.client.post(reverse("dashboard:member_retention"), {"departed_months": "12"})
        self.assertRedirects(response, reverse("dashboard:members"))
        self.assertEqual(MemberRetention.current().departed_months, 12)
        self.assertTrue(AuditLogEntry.objects.filter(action="member_retention.update", details="12 month(s)").exists())
        self.assertContains(self.client.get(reverse("dashboard:members")), "Departed members")

    def test_out_of_range_is_refused(self):
        self.client.post(reverse("dashboard:member_retention"), {"departed_months": "500"})
        self.assertEqual(MemberRetention.current().departed_months, 0)

    def test_managers_cannot(self):
        User.objects.create_user(username="manager", password="pw")
        self.client.login(username="manager", password="pw")
        self.client.post(reverse("dashboard:member_retention"), {"departed_months": "3"})
        self.assertEqual(MemberRetention.current().departed_months, 0)
