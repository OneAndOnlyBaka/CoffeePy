import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee.models import AuditLogEntry, CoffeeSort, Member, Order, Payment, PaymentMethod
from dashboard import import_jobs


class ResetDatabaseViewTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser(username="root", password="pw")
        self.url = reverse("dashboard:reset_database")
        self._status_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._status_dir.cleanup)
        self._settings = override_settings(
            IMPORT_STATUS_FILE=Path(self._status_dir.name) / "import.json"
        )
        self._settings.enable()
        self.addCleanup(self._settings.disable)

        sort = CoffeeSort.objects.create(name="espresso", price="0.30", machine_strokes=1)
        member = Member.objects.create(uid=42, nickname="Alice", favourite_coffee=sort)
        Order.objects.create(member=member, value="0.30", machine_strokes=1)
        Payment.objects.create(member=member, value="5.00")
        PaymentMethod.objects.create(name="PayPal", identifier="alice")

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_non_superuser_is_rejected(self):
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_page_lists_current_row_counts(self):
        self.client.login(username="root", password="pw")
        response = self.client.get(self.url)
        self.assertContains(response, "Members")
        self.assertContains(response, "Type <code>DELETE</code>")

    def test_confirmed_post_deletes_everything(self):
        self.client.login(username="root", password="pw")

        response = self.client.post(self.url, {"confirm": "DELETE"})

        self.assertContains(response, "Database reset")
        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(CoffeeSort.objects.count(), 0)
        self.assertEqual(PaymentMethod.objects.count(), 0)

    def test_reset_is_audit_logged_and_survives_the_wipe(self):
        self.client.login(username="root", password="pw")
        AuditLogEntry.objects.create(action="member.edit", actor="root")

        self.client.post(self.url, {"confirm": "DELETE"})

        entries = list(AuditLogEntry.objects.all())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].action, "database.reset")
        self.assertEqual(entries[0].actor, "root")

    def test_the_admin_stays_logged_in(self):
        self.client.login(username="root", password="pw")

        self.client.post(self.url, {"confirm": "DELETE"})

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_wrong_confirmation_deletes_nothing(self):
        self.client.login(username="root", password="pw")

        response = self.client.post(self.url, {"confirm": "delete please"})

        self.assertContains(response, "nothing was deleted")
        self.assertEqual(Member.objects.count(), 1)

    def test_reset_is_refused_while_an_import_runs(self):
        self.client.login(username="root", password="pw")
        import_jobs.write_status(state="running", phase="Orders", percent=10)

        response = self.client.post(self.url, {"confirm": "DELETE"})

        self.assertContains(response, "An import is running")
        self.assertEqual(Member.objects.count(), 1)
