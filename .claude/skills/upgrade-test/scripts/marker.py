# Backup test: a change made *after* a backup - a restore of that backup must take it away.
# Also rewrites an old order's value, so a restore that only adds missing rows would show up.
#   pi-device django.sh .claude/skills/upgrade-test/scripts/marker.py B0     set one, tagged
#   pi-device django.sh .claude/skills/upgrade-test/scripts/marker.py        which are there
import os
from decimal import Decimal

from coffee.models import CoffeeSort, Order

tag = os.environ.get("SCRIPT_ARGS", "").strip()
first = Order.objects.order_by("id").first()
if tag:
    CoffeeSort.objects.create(name=f"after-backup-{tag}", price=Decimal("9.99"), machine_strokes=1)
    first.value += Decimal("100")
    first.save(update_fields=["value"])
print("markers:", list(CoffeeSort.objects.filter(name__startswith="after-backup-").values_list("name", flat=True)),
      "| order", first.id, "value", first.value)
