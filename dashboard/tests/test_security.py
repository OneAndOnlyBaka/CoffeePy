import json
import shutil
import tempfile
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coffee.models import AuditLogEntry, CoffeeSort, Member, NfcScan
from dashboard import audit, security
from dashboard.models import AccountSecurity, PasswordPolicy

STRONG = "Kaffee-ist-fertig-42"


def login(client, username, password):
    return client.post(reverse("dashboard:login"), {"username": username, "password": password},
                       HTTP_X_FORWARDED_FOR="192.168.4.23")


class PasswordQualityTestCase(TestCase):
    def test_bsi_rule(self):
        validator = security.BSIPasswordValidator()
        for good in ("Kaffee-mit-Milch1", "kaffee mit milch und zucker", "Abcdefgh1234"):
            validator.validate(good)
        for bad in ("kaffeekaffee", "Kaffee12345", "kurz-Aber1", "nurkleinbuchstabenlang", "Aa1-" * 33):
            with self.assertRaises(ValidationError, msg=bad):
                validator.validate(bad)

    def test_follows_the_policy(self):
        PasswordPolicy.objects.update_or_create(pk=1, defaults={"min_length": 16, "min_kinds": 4, "passphrase_length": 0})
        validator = security.BSIPasswordValidator()
        with self.assertRaises(ValidationError):
            validator.validate("kaffee-mit-milch-1")  # long enough, but no upper case - and no passphrase rule
        validator.validate("Kaffee-mit-Milch-1")


class PolicyPageTestCase(TestCase):
    def setUp(self):
        User.objects.create_superuser(username="boss", password=STRONG)
        self.client = Client()
        login(self.client, "boss", STRONG)

    def post(self, **changes):
        data = {"min_length": 12, "min_kinds": 3, "passphrase_length": 20, "passphrase_kinds": 2,
                "lock_after_failures": 5, "lock_minutes": 15, "idle_logout_minutes": 30, **changes}
        return self.client.post(reverse("dashboard:password_policy"), data)

    def test_saves_and_logs_the_change(self):
        self.assertRedirects(self.post(min_length=14), reverse("dashboard:password_policy"))
        self.assertEqual(PasswordPolicy.current().min_length, 14)
        self.assertTrue(AuditLogEntry.objects.filter(action="security.policy_update", details__contains="min_length 12 -> 14").exists())

    def test_values_stay_within_sensible_limits(self):
        for bad in ({"min_length": 4}, {"min_length": 200}, {"passphrase_length": 300}, {"min_kinds": 5},
                    {"idle_logout_minutes": 0}, {"passphrase_length": 10}):
            self.assertEqual(self.post(**bad).status_code, 200, bad)
        self.assertEqual(PasswordPolicy.current().min_length, 12)

    def test_managers_cannot_change_it(self):
        User.objects.create_user(username="helper", password=STRONG)
        client = Client()
        login(client, "helper", STRONG)
        client.post(reverse("dashboard:password_policy"), {"min_length": 8})
        self.assertEqual(PasswordPolicy.current().min_length, 12)


class StricterRulesTestCase(TestCase):
    def test_a_password_too_weak_for_new_rules_must_be_replaced_at_the_next_login(self):
        User.objects.create_user(username="helper", password=STRONG)  # 20 characters, 4 kinds
        User.objects.create_user(username="careful", password="Ein-sehr-langes-Passwort-2026!")
        PasswordPolicy.objects.update_or_create(pk=1, defaults={"min_length": 24, "passphrase_length": 28})
        weak, strong = Client(), Client()
        login(weak, "helper", STRONG)
        self.assertRedirects(weak.get(reverse("dashboard:members")), reverse("dashboard:account"))
        self.assertTrue(AuditLogEntry.objects.filter(action="auth.password_outdated", target="helper").exists())
        login(strong, "careful", "Ein-sehr-langes-Passwort-2026!")
        self.assertEqual(strong.get(reverse("dashboard:members")).status_code, 200)


class LoginAuditTestCase(TestCase):
    def setUp(self):
        User.objects.create_user(username="helper", password=STRONG)
        self.client = Client()

    def test_success_and_failure_are_logged_with_address(self):
        login(self.client, "helper", "wrong")
        login(self.client, "helper", STRONG)
        failed = AuditLogEntry.objects.get(action="auth.login_failed")
        self.assertEqual((failed.target, failed.outcome, failed.ip, failed.source), ("helper", "failure", "192.168.4.23", "dashboard"))
        self.assertTrue(AuditLogEntry.objects.filter(action="auth.login", target="helper", outcome="success").exists())

    def test_locked_after_five_failures_even_with_the_right_password(self):
        for _ in range(PasswordPolicy.current().lock_after_failures):
            login(self.client, "helper", "wrong")
        response = login(self.client, "helper", STRONG)
        self.assertContains(response, "locked until")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditLogEntry.objects.filter(action="auth.locked", target="helper").exists())

    def test_the_lock_runs_out(self):
        for _ in range(PasswordPolicy.current().lock_after_failures):
            login(self.client, "helper", "wrong")
        AuditLogEntry.objects.update(created_at=timezone.now() - timedelta(minutes=PasswordPolicy.current().lock_minutes + 1))
        login(self.client, "helper", STRONG)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logged_out_after_inactivity(self):
        login(self.client, "helper", STRONG)
        session = self.client.session
        session["dashboard_last_seen"] = time.time() - PasswordPolicy.current().idle_logout_minutes * 60 - 1
        session.save()
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditLogEntry.objects.filter(action="auth.logout", details__contains="inactivity").exists())


class MustChangePasswordTestCase(TestCase):
    def setUp(self):
        User.objects.create_superuser(username="boss", password=STRONG)
        self.admin = Client()
        login(self.admin, "boss", STRONG)

    def test_new_user_has_to_set_their_own_password_first(self):
        self.admin.post(reverse("dashboard:user_add"), {
            "username": "helper", "role": "manager", "password1": STRONG, "password2": STRONG,
            "must_change_password": "on",
        })
        helper = Client()
        login(helper, "helper", STRONG)
        self.assertRedirects(helper.get(reverse("dashboard:members")), reverse("dashboard:account"))
        helper.post(reverse("dashboard:account"), {"old_password": STRONG, "new_password1": "Mein-eigenes-Passwort-7",
                                                   "new_password2": "Mein-eigenes-Passwort-7"})
        self.assertEqual(helper.get(reverse("dashboard:members")).status_code, 200)
        self.assertFalse(AccountSecurity.objects.get(user__username="helper").must_change_password)

    def test_the_new_password_must_differ(self):
        helper_user = User.objects.create_user(username="helper", password=STRONG)
        AccountSecurity.objects.create(user=helper_user, must_change_password=True)
        helper = Client()
        login(helper, "helper", STRONG)
        response = helper.post(reverse("dashboard:account"), {"old_password": STRONG, "new_password1": STRONG,
                                                              "new_password2": STRONG})
        self.assertContains(response, "must differ")

    def test_no_password_options_for_yourself_in_the_user_list(self):
        boss = User.objects.get(username="boss")
        self.assertNotContains(self.admin.get(reverse("dashboard:users")), reverse("dashboard:user_password", args=[boss.pk]))
        self.assertRedirects(self.admin.get(reverse("dashboard:user_password", args=[boss.pk])), reverse("dashboard:account"))


class ActivityTestCase(TestCase):
    def test_refused_admin_page_is_logged(self):
        User.objects.create_user(username="helper", password=STRONG)
        client = Client()
        login(client, "helper", STRONG)
        client.get(reverse("dashboard:backup"))
        entry = AuditLogEntry.objects.get(action="access.denied")
        self.assertEqual((entry.actor, entry.outcome), ("helper", "failure"))

    def test_kiosk_card_login_and_order_are_logged(self):
        sort = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.30"), machine_strokes=1)
        Member.objects.create(uid=1, nickname="Anna", favourite_coffee=sort)
        kiosk = Client()
        kiosk.get(reverse("kiosk:index"))
        NfcScan.objects.create(uid="01000000", event=NfcScan.EVENT_INSERTED)
        kiosk.get(reverse("kiosk:panel"))
        kiosk.post(reverse("kiosk:order_confirm"))
        self.assertEqual(list(AuditLogEntry.objects.filter(source="kiosk").order_by("id").values_list("action", "actor")),
                         [("kiosk.card_login", "Anna (uid 1)"), ("order.create", "Anna (uid 1)")])


class SearchTestCase(TestCase):
    def setUp(self):
        User.objects.create_superuser(username="boss", password=STRONG)
        self.client = Client()
        login(self.client, "boss", STRONG)
        audit.log_actor("Anna (uid 1)", "order.create", target="Anna (uid 1)", source="kiosk")
        audit.log_actor("backup service", "backup.automatic", details="No backup stick", outcome="failure")

    def test_search_and_filters(self):
        url = reverse("dashboard:audit_log")
        self.assertEqual([e.action for e in self.client.get(url, {"q": "anna"}).context["page"]], ["order.create"])
        self.assertEqual([e.action for e in self.client.get(url, {"source": "kiosk"}).context["page"]], ["order.create"])
        self.assertEqual([e.action for e in self.client.get(url, {"outcome": "failure"}).context["page"]], ["backup.automatic"])
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(len(self.client.get(url, {"from": tomorrow}).context["page"]), 0)

    def test_csv_export_is_itself_logged(self):
        response = self.client.get(reverse("dashboard:audit_log"), {"q": "anna", "format": "csv"})
        self.assertIn("order.create", response.content.decode())
        self.assertTrue(AuditLogEntry.objects.filter(action="audit.export").exists())


class HousekeepingTestCase(TestCase):
    def setUp(self):
        self.storage = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.storage)
        overrides = override_settings(STORAGE_DIR=self.storage)
        overrides.enable()
        self.addCleanup(overrides.disable)

    def test_host_events_are_collected(self):
        (self.storage / "events.jsonl").write_text(json.dumps({
            "time": time.time() - 60, "action": "backup.stick_removed", "target": "/dev/sda1", "outcome": "failure"}) + "\n")
        self.assertEqual(audit.ingest_host_events(), 1)
        entry = AuditLogEntry.objects.get(action="backup.stick_removed")
        self.assertEqual((entry.source, entry.outcome), ("system", "failure"))
        self.assertFalse((self.storage / "events.jsonl").exists())

    def test_old_entries_are_deleted_after_the_retention_period(self):
        old = AuditLogEntry.objects.create(action="member.create")
        AuditLogEntry.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
        AuditLogEntry.objects.create(action="member.update")
        self.assertEqual(audit.prune(90), 1)
        self.assertEqual(list(AuditLogEntry.objects.values_list("action", flat=True)), ["member.update"])
