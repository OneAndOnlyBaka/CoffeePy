import re
import sqlite3
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from coffee.models import CoffeeSort, Member, MemberCard, Order, Payment
from coffee.purge import IMPORT_SCOPE, describe, purge

SQLITE_MAGIC = b"SQLite format 3\x00"


def open_source(path: str) -> sqlite3.Connection:
    """Open `path` as a SQLite connection. Accepts either a real SQLite database file
    (coffee.db) or a plain SQL dump/text file - the legacy app's own backups are named
    "coffee<N>.db" but actually contain sqlite3.iterdump() text, not a SQLite binary file."""
    with open(path, "rb") as f:
        header = f.read(len(SQLITE_MAGIC))

    if header == SQLITE_MAGIC:
        return sqlite3.connect(path)

    con = sqlite3.connect(":memory:")
    with open(path, "r", encoding="utf-8") as f:
        con.executescript(f.read())
    return con


LEGACY_SORT_ID_RE = re.compile(r"\[(\d+)\]")

# How the legacy app worded an order's info - "Ordered ID [3]". Today's kiosk writes
# "Ordered [<pk>] <name>" instead, so the prefix tells legacy rows apart from current ones
# (see backfill_order_sorts, which must only ever repair the legacy ones).
LEGACY_INFO_PREFIX = "Ordered ID"


def legacy_sort_id(info) -> int | None:
    """Which coffee sort a legacy order was for. The legacy `coffee_order` table has no
    column for it - it only appears inside the free-text info ("Ordered ID [3]"), where
    the number is the coffee_sorts rowid. Dropping it leaves every imported order without
    a sort, which empties the distribution and price-trend charts on the statistics page."""
    match = LEGACY_SORT_ID_RE.search(info or "")
    return int(match.group(1)) if match else None


def _parse_datetime(value, fallback_timestamp=None):
    if value:
        try:
            return timezone.make_aware(datetime.fromisoformat(value))
        except ValueError:
            pass
    if fallback_timestamp:
        return datetime.fromtimestamp(int(fallback_timestamp), tz=timezone.get_current_timezone())
    return timezone.now()


class Command(BaseCommand):
    help = (
        "Import members, coffee sorts, orders and payments from a legacy CoffeePy "
        "SQLite database or SQL dump file."
    )

    # Optional callable(phase, done, total) that in-process callers can set to follow a
    # long import row by row - the dashboard uses it to drive its progress bar. Unset on
    # the command line, where the plain summary line is enough.
    progress = None

    #: Rows between two progress callbacks - fine-grained enough for a smooth bar
    #: without calling back on every single insert.
    TICK_EVERY = 25

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Path to coffee.db or a SQL dump")
        parser.add_argument(
            "--purge",
            action="store_true",
            help=(
                "Delete existing members, cards, orders, payments and coffee sorts first, "
                "so the import starts from a clean slate instead of merging into them."
            ),
        )

    def handle(self, *args, **options):
        path = options["path"]
        try:
            con = open_source(path)
        except OSError as exc:
            raise CommandError(f"Could not open {path}: {exc}")

        con.row_factory = sqlite3.Row
        cur = con.cursor()

        self._done = 0
        self._phase = "Reading source"
        self._total = self._count_rows(cur)
        self._report()

        with transaction.atomic():
            if options["purge"]:
                self._start_phase("Clearing existing data")
                cleared = purge(IMPORT_SCOPE)
                self.stdout.write(f"Cleared {describe(cleared)} before importing.")
            sort_id_map = self._import_coffee_sorts(cur)
            self._import_members(cur, sort_id_map)
            n_orders = self._import_orders(cur, sort_id_map)
            n_payments = self._import_payments(cur)

        con.close()
        self._phase = "Done"
        self._done = self._total
        self._report()
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(sort_id_map)} coffee sorts, "
                f"{Member.objects.count()} members, {n_orders} orders, {n_payments} payments."
            )
        )

    def _count_rows(self, cur) -> int:
        """Total number of source rows, so progress can be reported as a percentage."""
        total = 0
        for table in ("coffee_sorts", "user", "coffee_order", "payments"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed table names
            total += cur.fetchone()[0]
        return total

    def _start_phase(self, phase: str):
        self._phase = phase
        self._report()

    def _tick(self):
        self._done += 1
        if self._done % self.TICK_EVERY == 0:
            self._report()

    def _report(self):
        if self.progress:
            self.progress(self._phase, self._done, self._total)

    def _import_coffee_sorts(self, cur) -> dict:
        self._start_phase("Coffee sorts")
        cur.execute("SELECT rowid, coffee_name, coffee_price, coffee_machine_strokes FROM coffee_sorts")
        sort_id_map = {}
        for row in cur.fetchall():
            self._tick()
            sort, _ = CoffeeSort.objects.update_or_create(
                name=row["coffee_name"],
                defaults={
                    "price": row["coffee_price"],
                    "machine_strokes": row["coffee_machine_strokes"],
                },
            )
            sort_id_map[row["rowid"]] = sort.pk
        return sort_id_map

    def _import_members(self, cur, sort_id_map: dict):
        self._start_phase("Members")
        cur.execute("SELECT user_uid, user_alt_uid, user_nick_name, user_favourite_coffee FROM user")
        for row in cur.fetchall():
            self._tick()
            favourite_pk = sort_id_map.get(row["user_favourite_coffee"])
            member, _ = Member.objects.update_or_create(
                uid=row["user_uid"],
                defaults={
                    "nickname": row["user_nick_name"] or "",
                    "favourite_coffee_id": favourite_pk,
                },
            )
            if row["user_alt_uid"] is not None:
                MemberCard.objects.get_or_create(uid=row["user_alt_uid"], defaults={"member": member})

    def _import_orders(self, cur, sort_id_map: dict) -> int:
        cur.execute(
            "SELECT user_uid, order_value, order_coffee_machine_strokes, "
            "order_timestamp, order_datetime, order_info FROM coffee_order"
        )
        count = 0
        self._start_phase("Orders")
        for row in cur.fetchall():
            self._tick()
            if not Member.objects.filter(uid=row["user_uid"]).exists():
                continue
            Order.objects.create(
                member_id=row["user_uid"],
                coffee_sort_id=sort_id_map.get(legacy_sort_id(row["order_info"])),
                value=row["order_value"],
                machine_strokes=row["order_coffee_machine_strokes"],
                created_at=_parse_datetime(row["order_datetime"], row["order_timestamp"]),
                info=row["order_info"] or "",
            )
            count += 1
        return count

    def _import_payments(self, cur) -> int:
        cur.execute(
            "SELECT user_uid, payment_value, payment_timestamp, payment_datetime, "
            "payment_info FROM payments"
        )
        count = 0
        self._start_phase("Payments")
        for row in cur.fetchall():
            self._tick()
            if not Member.objects.filter(uid=row["user_uid"]).exists():
                continue
            Payment.objects.create(
                member_id=row["user_uid"],
                value=row["payment_value"],
                created_at=_parse_datetime(row["payment_datetime"], row["payment_timestamp"]),
                info=row["payment_info"] or "",
            )
            count += 1
        return count
