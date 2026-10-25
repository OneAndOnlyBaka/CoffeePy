"""Believable data for the manual's screenshots - run through the pi-device skill's django.sh.

    django.sh ../../user-manual/scripts/demo_data.py seed
    django.sh ../../user-manual/scripts/demo_data.py scan Anna      the kiosk sees Anna's card
    django.sh ../../user-manual/scripts/demo_data.py scan new       ... a card it doesn't know
    django.sh ../../user-manual/scripts/demo_data.py cleanup

Six members with card-sized uids (so a scan can log the kiosk in as them), a year of orders,
payments, one member in the pillory, two coffee sorts and a PayPal method for the QR codes.
Everything it creates is recognisable - the members and sorts by name, the scans by their
uid prefix - and cleanup removes exactly that, so real data on the device stays untouched.

"scan" writes an NfcScan row, which the LIVE kiosk picks up on its next poll - that is the
point here, and the reason cleanup must only run once the kiosk has been logged out again
(a nav tap does that).
"""
import os
import random
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from coffee.models import CoffeeSort, Member, NfcScan, Order, Payment, PaymentMethod

MEMBERS = ["Anna", "Ben", "Clara", "David", "Emma", "Felix"]
# NfcScan hex is little-endian (NfcScan.uid_as_int): "C0FFEE01" is member 0x01EEFFC0.
CARD_PREFIX = "C0FFEE"
NEW_CARD = "C0FFEEFF"
SORTS = [("Espresso", "0.30", 1), ("Cappuccino", "0.50", 1), ("Kanne · Jug", "0.60", 2)]
PAYPAL = "PayPal"


def card_hex(index):
    return f"{CARD_PREFIX}{index + 1:02X}"


def uid(index):
    return int.from_bytes(bytes.fromhex(card_hex(index)), "little")


def seed():
    if Member.objects.filter(uid=uid(0)).exists():
        print("already seeded")
        return
    random.seed(7)
    sorts = [CoffeeSort.objects.get_or_create(name=n, defaults={"price": Decimal(p), "machine_strokes": s})[0]
             for n, p, s in SORTS]
    favourites = [sorts[0], sorts[1], sorts[0], sorts[2], sorts[1], sorts[0]]
    members = [Member.objects.create(uid=uid(i), nickname=n, favourite_coffee=favourites[i])
               for i, n in enumerate(MEMBERS)]
    thirst = [4, 3, 2, 2, 1, 1]  # cups on a busy day - Anna leads every ranking
    now = timezone.now()
    orders, payments = [], []
    for day in range(1, 365):
        d = now - timedelta(days=day)
        if d.weekday() >= 5:
            continue
        for member, cups in zip(members, thirst):
            for _ in range(random.randint(0, cups)):
                sort = random.choice(sorts) if random.random() < 0.3 else member.favourite_coffee
                at = d.replace(hour=random.choice([7, 8, 8, 9, 10, 12, 13, 13, 14, 15, 16]), minute=random.randint(0, 59))
                orders.append(Order(member=member, coffee_sort=sort, value=sort.price,
                                    machine_strokes=sort.machine_strokes, created_at=at,
                                    info=f"Ordered [{sort.pk}] {sort.name}"))
    Order.objects.bulk_create(orders)
    for member in members:
        owed = sum(o.value for o in orders if o.member_id == member.uid)
        # Everyone paid up except David, who stopped paying three months ago (the pillory).
        paid = owed if member.nickname != "David" else owed - Decimal("18.40")
        payments.append(Payment(member=member, value=paid.quantize(Decimal("0.01")),
                                created_at=now - timedelta(days=95 if member.nickname == "David" else 3)))
    Payment.objects.bulk_create(payments)
    PaymentMethod.objects.get_or_create(name=PAYPAL, defaults={"provider": PaymentMethod.PROVIDER_PAYPAL_ME,
                                                               "identifier": "coffeeclub"})
    print(f"seeded {len(members)} members, {len(orders)} orders, {len(payments)} payments")


def scan(who):
    hex_uid = NEW_CARD if who == "new" else card_hex(MEMBERS.index(who))
    NfcScan.objects.create(uid=hex_uid, event=NfcScan.EVENT_INSERTED)
    print(f"scanned {hex_uid}")


def cleanup():
    uids = [uid(i) for i in range(len(MEMBERS))]
    print("members:", Member.objects.filter(uid__in=uids).delete()[0])
    print("scans:", NfcScan.objects.filter(uid__startswith=CARD_PREFIX).delete()[0])
    print("sorts:", CoffeeSort.objects.filter(name__in=[n for n, _, _ in SORTS], order__isnull=True).delete()[0])
    print("payment method:", PaymentMethod.objects.filter(name=PAYPAL, identifier="coffeeclub").delete()[0])


args = os.environ.get("SCRIPT_ARGS", "").split()
{"seed": lambda: seed(), "scan": lambda: scan(args[1]), "cleanup": lambda: cleanup()}[args[0]]()
