from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class WeroMigrationTestCase(TransactionTestCase):
    """coffee/0014: WERO methods go, the payments made with them stay (they carry the name)."""

    def test_wero_methods_are_removed_and_their_payments_stay(self):
        executor = MigrationExecutor(connection)
        before, after = [("coffee", "0013_purchase_from_the_coffee_box")], [("coffee", "0014_girocode_instead_of_wero")]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        apps.get_model("coffee", "PaymentMethod").objects.create(name="WERO", provider="wero", identifier="+49 1")
        apps.get_model("coffee", "PaymentMethod").objects.create(name="PayPal", provider="paypal_me", identifier="x")
        member = apps.get_model("coffee", "Member").objects.create(uid=1, nickname="Alice X.")
        apps.get_model("coffee", "Payment").objects.create(member=member, value=5, method_name="WERO")
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        self.assertEqual(list(apps.get_model("coffee", "PaymentMethod").objects.values_list("name", flat=True)), ["PayPal"])
        self.assertEqual(apps.get_model("coffee", "Payment").objects.get().method_name, "WERO")
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
