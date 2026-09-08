import sqlite3
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from coffee.management.commands.import_sqlite import Command as ImportSqliteCommand
from coffee.models import CoffeeSort, Member, MemberCard, Order, Payment

LEGACY_SCHEMA = """
CREATE TABLE coffee_sorts(coffee_name TEXT, coffee_price REAL, coffee_machine_strokes INT);
CREATE TABLE user(user_uid INTEGER PRIMARY KEY, user_alt_uid INTEGER UNIQUE DEFAULT NULL,
                  user_nick_name TEXT, user_favourite_coffee INT);
CREATE TABLE coffee_order(user_uid INTEGER, order_value REAL, order_coffee_machine_strokes INT,
                          order_timestamp INT, order_datetime TEXT, order_info TEXT);
CREATE TABLE payments(user_uid INTEGER, payment_value REAL, payment_timestamp INT,
                      payment_datetime TEXT, payment_info TEXT);
INSERT INTO coffee_sorts VALUES ('espresso', 0.3, 1);
INSERT INTO coffee_sorts VALUES ('jug', 0.6, 2);
INSERT INTO user VALUES (42, NULL, 'Alice', 1);
INSERT INTO coffee_order VALUES (42, 0.3, 1, 1700000000, '2023-11-14 22:13:20', 'Ordered ID [1]');
INSERT INTO coffee_order VALUES (42, 0.6, 2, 1700000100, '2023-11-14 22:15:00', 'Ordered ID [2]');
INSERT INTO payments VALUES (42, 5.0, 1700000000, '2023-11-14 22:13:20', '');
"""


class ImportSqliteTestCase(TestCase):
    def _assert_imported(self):
        self.assertEqual(CoffeeSort.objects.count(), 2)
        espresso = CoffeeSort.objects.get(name="espresso")
        self.assertEqual(espresso.machine_strokes, 1)

        alice = Member.objects.get(uid=42)
        self.assertEqual(alice.nickname, "Alice")
        self.assertEqual(alice.favourite_coffee, espresso)

        self.assertEqual(Order.objects.count(), 2)
        order = Order.objects.get(value="0.30")
        self.assertEqual(order.member_id, 42)
        # The legacy schema has no coffee-sort column on coffee_order - the sort only
        # exists as the rowid in "Ordered ID [1]", and without it the statistics page's
        # distribution and price-trend charts stay empty.
        self.assertEqual(order.coffee_sort, espresso)
        self.assertEqual(Order.objects.get(value="0.60").coffee_sort, CoffeeSort.objects.get(name="jug"))

        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Payment.objects.get().member_id, 42)

    def test_import_from_real_sqlite_db_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "coffee.db"
            con = sqlite3.connect(db_path)
            con.executescript(LEGACY_SCHEMA)
            con.commit()
            con.close()

            call_command("import_sqlite", path=str(db_path))
            self._assert_imported()

    def test_import_from_sql_dump_text_file(self):
        # The legacy app's own backups are named "coffeeN.db" but are actually
        # sqlite3.iterdump() SQL text, not a SQLite binary file - import_sqlite must
        # handle both.
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "coffee_backup.db"
            dump_path.write_text(LEGACY_SCHEMA)

            call_command("import_sqlite", path=str(dump_path))
            self._assert_imported()

    def test_purge_flag_clears_data_the_dump_does_not_contain(self):
        Member.objects.create(uid=7, nickname="Bob")
        CoffeeSort.objects.create(name="filter", price="0.20", machine_strokes=1)

        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "coffee_backup.db"
            dump_path.write_text(LEGACY_SCHEMA)

            call_command("import_sqlite", path=str(dump_path), purge=True)

            self.assertFalse(Member.objects.filter(uid=7).exists())
            self.assertFalse(CoffeeSort.objects.filter(name="filter").exists())
            self._assert_imported()

    def test_progress_hook_reports_every_phase_and_ends_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "coffee_backup.db"
            dump_path.write_text(LEGACY_SCHEMA)

            reports = []
            command = ImportSqliteCommand()
            command.progress = lambda phase, done, total: reports.append((phase, done, total))
            call_command(command, path=str(dump_path))

            phases = [phase for phase, _, _ in reports]
            self.assertEqual(
                phases[:5], ["Reading source", "Coffee sorts", "Members", "Orders", "Payments"]
            )
            self.assertEqual(reports[-1], ("Done", 6, 6))  # 2 sorts + 1 member + 2 orders + 1 payment

    def test_order_without_a_recognisable_sort_id_still_imports(self):
        schema = LEGACY_SCHEMA + (
            "INSERT INTO coffee_order VALUES "
            "(42, 0.3, 1, 1700000200, '2023-11-14 22:20:00', 'imported by hand');\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "coffee_backup.db"
            dump_path.write_text(schema)

            call_command("import_sqlite", path=str(dump_path))

            self.assertEqual(Order.objects.count(), 3)
            self.assertEqual(Order.objects.filter(coffee_sort__isnull=True).count(), 1)

    def test_legacy_alt_uid_becomes_a_membercard(self):
        schema = """
CREATE TABLE coffee_sorts(coffee_name TEXT, coffee_price REAL, coffee_machine_strokes INT);
CREATE TABLE user(user_uid INTEGER PRIMARY KEY, user_alt_uid INTEGER UNIQUE DEFAULT NULL,
                  user_nick_name TEXT, user_favourite_coffee INT);
CREATE TABLE coffee_order(user_uid INTEGER, order_value REAL, order_coffee_machine_strokes INT,
                          order_timestamp INT, order_datetime TEXT, order_info TEXT);
CREATE TABLE payments(user_uid INTEGER, payment_value REAL, payment_timestamp INT,
                      payment_datetime TEXT, payment_info TEXT);
INSERT INTO coffee_sorts VALUES ('espresso', 0.3, 1);
INSERT INTO user VALUES (42, 99, 'Alice', 1);
"""
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "coffee_backup.db"
            dump_path.write_text(schema)

            call_command("import_sqlite", path=str(dump_path))
            alice = Member.objects.get(uid=42)
            self.assertTrue(MemberCard.objects.filter(member=alice, uid=99).exists())
