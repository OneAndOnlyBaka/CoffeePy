"""Fills in the coffee sort of legacy orders that were imported without one.

An import done before import_sqlite learned to read the sort out of the order info text
left every legacy order with `coffee_sort` NULL, which shows on the statistics page as an
empty distribution chart and a price trend with no lines at all. Re-importing is not a fix
for that - it would create every order a second time - so this repairs the rows in place.

The legacy database is still needed, but only for its `coffee_sorts` table: the ids in
"Ordered ID [3]" are rowids in *that* table, and nothing in the imported data records what
they pointed at.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from coffee.models import CoffeeSort, Order

from .import_sqlite import LEGACY_INFO_PREFIX, open_source


class Command(BaseCommand):
    help = (
        "Repair legacy orders imported without a coffee sort, using the coffee_sorts "
        "table of the original database."
    )

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Path to the original coffee.db or SQL dump")
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be repaired, change nothing"
        )

    def handle(self, *args, **options):
        try:
            con = open_source(options["path"])
        except OSError as exc:
            raise CommandError(f"Could not open {options['path']}: {exc}")

        cur = con.cursor()
        cur.execute("SELECT rowid, coffee_name FROM coffee_sorts")
        legacy_sorts = cur.fetchall()
        con.close()

        # Only ever touch rows written by the legacy import: today's kiosk records
        # "Ordered [<pk>] <name>" with a *Django* pk, and one of those can end up sort-less
        # too (deleting a coffee sort nulls its orders). Mapping such a row through legacy
        # rowids would silently file it under the wrong coffee.
        repairable = Order.objects.filter(coffee_sort__isnull=True, info__startswith=LEGACY_INFO_PREFIX)
        before = repairable.count()

        repaired = 0
        with transaction.atomic():
            for rowid, name in legacy_sorts:
                sort = CoffeeSort.objects.filter(name=name).first()
                if sort is None:
                    self.stdout.write(self.style.WARNING(f"No coffee sort named '{name}' - skipped."))
                    continue
                matching = repairable.filter(info__contains=f"[{rowid}]")
                count = matching.count()
                if not options["dry_run"]:
                    matching.update(coffee_sort=sort)
                repaired += count
                self.stdout.write(f"{name}: {count} orders")

        left = before - repaired
        verb = "Would repair" if options["dry_run"] else "Repaired"
        self.stdout.write(self.style.SUCCESS(f"{verb} {repaired} of {before} legacy orders ({left} unmatched)."))
