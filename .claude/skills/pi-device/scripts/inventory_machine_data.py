"""Test data for the inventory next to a real coffee machine - django.sh inventory_machine_data.py seed|report|cleanup.

seed: two items of their own ("Testbohnen" in kg, "Testmilch" in l) with the machine's suggested consumption per
product (coffee.machine.consumption_suggestions), half a year of purchases paid from the coffee box (no member's
balance moves) and a stock-take now - so the next brews at the machine show up as "expected" stock going down.
report: what the inventory makes of it - expected stock, the machine's cups, cost per cup.
cleanup: the two items with all of theirs. A device's own items are never touched.
"""
import os
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from coffee import machine, services
from coffee.models import InventoryConsumption, InventoryItem, MachineProduct, Purchase, StockCount

ITEMS = {"Testbohnen": ("kg", Decimal("1"), Decimal("15.99"), Decimal("1.5")),  # unit, per purchase, price, counted
         "Testmilch": ("l", Decimal("6"), Decimal("6.54"), Decimal("4"))}
TAG = "inventory_machine_data"


def seed():
    now = timezone.now()
    for name, (unit, quantity, price, counted) in ITEMS.items():
        item, created = InventoryItem.objects.get_or_create(name=name, defaults={"unit": unit, "order": 90})
        if not created:
            print("already there:", name)
            continue
        for values in machine.consumption_suggestions(unit).values():
            for code, amount in values.items():
                InventoryConsumption.objects.create(item=item, product=MachineProduct.objects.get(code=code), amount=amount)
        for weeks in range(26, 0, -2):  # every second week, a little dearer over time
            services.book_purchase(None, item, quantity, (price * (Decimal("1") - Decimal(weeks) / 200)).quantize(
                Decimal("0.01")), info=TAG, created_at=now - timedelta(weeks=weeks))
        StockCount.objects.create(item=item, quantity=counted, counted_by=TAG, created_at=now)
        print("seeded", name, "consumption:", list(item.consumption.values_list("product__name", "amount")))


def report():
    for level in services.stock_levels(InventoryItem.objects.filter(name__in=ITEMS)):
        print(f"{level['item'].name}: counted {level['count'].quantity if level['count'] else '-'}, "
              f"used by the machine since {level['machine_used']}, expected {level['expected']}")
    since = timezone.now() - timedelta(days=30)
    print("machine cups 30 days:", services.machine_cups(since), "booked:", services._booked_cups(since))
    cost = services.cost_per_cup(6)
    print("cost per cup (6 months):", {k: cost[k] for k in ("cups", "booked_cups", "cups_from_machine") if k in cost},
          cost.get("per_cup"))


def cleanup():
    items = InventoryItem.objects.filter(name__in=ITEMS)
    print("purchases:", Purchase.objects.filter(item__in=items).delete()[0])
    print("items:", items.delete()[0])


{"seed": seed, "report": report, "cleanup": cleanup}[os.environ.get("SCRIPT_ARGS", "report").split()[0]]()
