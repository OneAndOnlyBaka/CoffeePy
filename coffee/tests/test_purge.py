from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.utils import timezone

from coffee.models import (
    AuditLogEntry,
    CoffeeSort,
    Member,
    MemberCard,
    MemberDevice,
    NfcScan,
    Order,
    Payment,
    PaymentMethod,
)
from coffee.purge import FULL_SCOPE, IMPORT_SCOPE, describe, purge


class PurgeTestCase(TestCase):
    def setUp(self):
        self.sort = CoffeeSort.objects.create(name="espresso", price="0.30", machine_strokes=1)
        self.member = Member.objects.create(uid=42, nickname="Alice X.", favourite_coffee=self.sort)
        MemberCard.objects.create(member=self.member, uid=99)
        Order.objects.create(member=self.member, value="0.30", machine_strokes=1)
        Payment.objects.create(member=self.member, value="5.00")
        PaymentMethod.objects.create(name="PayPal", identifier="alice")
        NfcScan.objects.create(uid="deadbeef", event=NfcScan.EVENT_INSERTED)
        AuditLogEntry.objects.create(action="member.edit", actor="root")

    def test_import_scope_clears_what_an_import_refills(self):
        counts = purge(IMPORT_SCOPE)

        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(MemberCard.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(CoffeeSort.objects.count(), 0)
        self.assertEqual(counts["coffee.Member"], 1)
        self.assertEqual(counts["coffee.Order"], 1)

    def test_import_scope_keeps_payment_methods_scans_and_audit_log(self):
        purge(IMPORT_SCOPE)

        self.assertEqual(PaymentMethod.objects.count(), 1)
        self.assertEqual(NfcScan.objects.count(), 1)
        self.assertEqual(AuditLogEntry.objects.count(), 1)

    def test_full_scope_clears_everything_coffee_related(self):
        purge(FULL_SCOPE)

        self.assertEqual(PaymentMethod.objects.count(), 0)
        self.assertEqual(NfcScan.objects.count(), 0)
        self.assertEqual(AuditLogEntry.objects.count(), 0)
        self.assertEqual(Member.objects.count(), 0)

    def test_dashboard_logins_survive_a_full_purge(self):
        User.objects.create_superuser(username="root", password="pw")

        purge(FULL_SCOPE)

        self.assertTrue(User.objects.filter(username="root").exists())

    def test_linked_phone_sessions_are_logged_out(self):
        member_session = Session.objects.create(
            session_key="member-session", session_data="", expire_date=timezone.now()
        )
        admin_session = Session.objects.create(
            session_key="admin-session", session_data="", expire_date=timezone.now()
        )
        MemberDevice.objects.create(member=self.member, session_key=member_session.pk)

        purge(FULL_SCOPE)

        self.assertFalse(Session.objects.filter(pk=member_session.pk).exists())
        self.assertTrue(Session.objects.filter(pk=admin_session.pk).exists())

    def test_describe_names_the_deleted_rows(self):
        self.assertEqual(describe({}), "nothing")
        self.assertIn("1 member", describe(purge(IMPORT_SCOPE)))
