"""Synthetic history for performance work on the Pi - run through django.sh.

    django.sh perf_data.py seed      40 members, 5 years of weekday orders (~29k), ~1.7k payments
    django.sh perf_data.py cleanup   delete every one of them again (cascades their orders/payments)

All synthetic members are recognisable by uid: 9_000_000_000 and up (too big for a 4-byte
card, so no real tap can ever log into one), plus PERF_CARD_UID, the one member with a
card-sized uid that page_timings.py logs into. Real members are never touched.

Never creates NfcScan rows: the scan table is global, and the live kiosk polls it - a
synthetic scan logs the real touchscreen in as whoever it names.
"""
import os
import random
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from coffee.models import CoffeeSort, Member, Order, Payment

BASE_UID = 9_000_000_000
PERF_CARD_UID = 0x44332211
action = os.environ.get("SCRIPT_ARGS", "").strip()
synthetic = Member.objects.filter(Q(uid__gte=BASE_UID) | Q(uid=PERF_CARD_UID))

if action == "seed":
    if synthetic.exists():
        print("already seeded - run cleanup first")
    else:
        random.seed(1)
        sorts = list(CoffeeSort.objects.all())
        if not sorts:
            sorts = [CoffeeSort.objects.create(name="Perf coffee", price=Decimal("0.30"), machine_strokes=1),
                     CoffeeSort.objects.create(name="Perf jug", price=Decimal("0.60"), machine_strokes=2)]
        members = [Member(uid=BASE_UID + i, nickname=f"Perf {i:02d}") for i in range(39)]
        members.append(Member(uid=PERF_CARD_UID, nickname="Perf card"))
        Member.objects.bulk_create(members)
        now, orders, payments = timezone.now(), [], []
        for day in range(5 * 365):
            d = now - timedelta(days=day)
            if d.weekday() >= 5:
                continue
            for _ in range(random.randint(15, 30)):
                m, s = random.choice(members), random.choice(sorts)
                orders.append(Order(member=m, coffee_sort=s, value=s.price, machine_strokes=s.machine_strokes,
                                    created_at=d.replace(hour=random.randint(7, 17), minute=random.randint(0, 59)),
                                    info=f"Ordered [{s.pk}] {s.name}"))
            if day % 3 == 0:
                payments += [Payment(member=random.choice(members), value=Decimal(random.choice([5, 10, 20])),
                                     created_at=d) for _ in range(4)]
        Order.objects.bulk_create(orders, batch_size=5000)
        Payment.objects.bulk_create(payments, batch_size=5000)
elif action == "cleanup":
    print("deleted:", synthetic.delete()[1])
    CoffeeSort.objects.filter(name__startswith="Perf ").delete()
else:
    raise SystemExit(__doc__)

print("now:", Member.objects.count(), "members,", Order.objects.count(), "orders,", Payment.objects.count(), "payments")
