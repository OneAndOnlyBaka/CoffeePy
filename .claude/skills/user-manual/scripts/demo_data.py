"""Believable data for the manual's screenshots - run through the pi-device skill's django.sh.

    django.sh ../../user-manual/scripts/demo_data.py seed
    django.sh ../../user-manual/scripts/demo_data.py scan Anna      the kiosk sees Anna's card
    django.sh ../../user-manual/scripts/demo_data.py scan new       ... a card it doesn't know
    django.sh ../../user-manual/scripts/demo_data.py inventory on   ... purchases and stock (see below)
    django.sh ../../user-manual/scripts/demo_data.py inventory off
    django.sh ../../user-manual/scripts/demo_data.py cleanup

Six members with card-sized uids (so a scan can log the kiosk in as them), a year of orders,
payments, one member in the pillory, two coffee sorts and a PayPal method for the QR codes.
Everything it creates is recognisable - the members and sorts by name, the scans by their
uid prefix - and cleanup removes exactly that, so real data on the device stays untouched.

The inventory is off while the other screens are taken - the way a device starts, and the way
every screenshot without it looks (no "Purchase" button, no "Inventory" in the sidebar). seed
switches it off and remembers what it was, cleanup puts that back. "inventory on" switches it on
for members and adds a year of purchases - some from the coffee box - two stock-takes and Anna as
the one who may take stock; "inventory off" takes exactly that away again.

"scan" writes an NfcScan row, which the LIVE kiosk picks up on its next poll - that is the
point here, and the reason cleanup must only run once the kiosk has been logged out again
(a nav tap does that).
"""
import os
import random
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from coffee import services
from coffee.models import CoffeeSort, InventoryItem, Member, NfcScan, Order, Payment, PaymentMethod, Purchase, StockCount
from dashboard.models import InventoryPolicy

MEMBERS = ["Anna", "Ben", "Clara", "David", "Emma", "Felix"]
# What they are called - in the form the kiosk insists on (coffee/names.py), or every demo
# scan would end on its "Who are you?" screen instead of the one being captured.
NAMES = {"Anna": "Anna B.", "Ben": "Ben K.", "Clara": "Clara M.", "David": "David S.", "Emma": "Emma W.",
         "Felix": "Felix H."}
# NfcScan hex is little-endian (NfcScan.uid_as_int): "C0FFEE01" is member 0x01EEFFC0.
CARD_PREFIX = "C0FFEE"
NEW_CARD = "C0FFEEFF"
SORTS = [("Espresso", "0.30", 1), ("Cappuccino", "0.50", 1), ("Kanne · Jug", "0.60", 2)]
PAYPAL = "PayPal"
# (name, unit, quantity, price at the start of the year and a year later, every n days)
ITEMS = [("Kaffeebohnen · Coffee beans", "kg", "1", "14.49", "16.99", 30),
         ("Milch · Milk", "l", "6", "6.24", "7.14", 14),
         ("Kekse · Cookies", "", "2", "2.29", "2.59", 30)]
# Where the mode was before seed switched the inventory off - in the web container, which every
# django.sh call of one capture run shares.
MODE_FILE = "/tmp/manual-inventory-mode"


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
    members = [Member.objects.create(uid=uid(i), nickname=NAMES[n], favourite_coffee=favourites[i])
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
        paid = owed if member.nickname != NAMES["David"] else owed - Decimal("18.40")
        payments.append(Payment(member=member, value=paid.quantize(Decimal("0.01")),
                                created_at=now - timedelta(days=95 if member.nickname == NAMES["David"] else 3)))
    Payment.objects.bulk_create(payments)
    PaymentMethod.objects.get_or_create(name=PAYPAL, defaults={"provider": PaymentMethod.PROVIDER_PAYPAL_ME,
                                                               "identifier": "coffeefund"})
    print(f"seeded {len(members)} members, {len(orders)} orders, {len(payments)} payments")
    policy = InventoryPolicy.current()
    with open(MODE_FILE, "w") as f:
        f.write(policy.mode)
    policy.mode = InventoryPolicy.MODE_OFF
    policy.save()


def inventory_on():
    if InventoryItem.objects.filter(name=ITEMS[0][0]).exists():
        print("inventory already on")
        return
    now = timezone.now()
    payers = [Member.objects.get(uid=uid(i)) for i in range(3)] + [None]  # every fourth from the coffee box
    count = 0
    for order, (name, unit, quantity, first, last, every) in enumerate(ITEMS):
        item = InventoryItem.objects.create(name=name, unit=unit, order=order)
        for k, day in enumerate(range(360, 0, -every)):
            price = Decimal(first) + (Decimal(last) - Decimal(first)) * (360 - day) / 360  # dearer by the month
            services.book_purchase(payers[(k + order) % len(payers)], item, Decimal(quantity),
                                   price.quantize(Decimal("0.01")), created_at=now - timedelta(days=day, hours=3))
            count += 1
    beans, milk = InventoryItem.objects.get(name=ITEMS[0][0]), InventoryItem.objects.get(name=ITEMS[1][0])
    StockCount.objects.create(item=beans, quantity=Decimal("1.5"), counted_by="Anna B.", created_at=now - timedelta(days=150))
    StockCount.objects.create(item=beans, quantity=Decimal("0.8"), counted_by="Anna B.", created_at=now - timedelta(days=10))
    StockCount.objects.create(item=milk, quantity=Decimal("4"), counted_by="Anna B.", created_at=now - timedelta(days=10))
    Member.objects.filter(uid=uid(0)).update(may_take_stock=True)
    InventoryPolicy.objects.filter(pk=InventoryPolicy.current().pk).update(mode=InventoryPolicy.MODE_MEMBERS)
    print(f"inventory on: {len(ITEMS)} items, {count} purchases")


def inventory_off():
    items = InventoryItem.objects.filter(name__in=[name for name, *_ in ITEMS])
    purchases = Purchase.objects.filter(item__in=items)
    print("purchase credits:", Payment.objects.filter(purchase__in=purchases).delete()[0])
    print("purchases:", purchases.delete()[0])
    print("items:", items.delete()[0])  # their stock-takes with them
    Member.objects.filter(uid__in=[uid(i) for i in range(len(MEMBERS))]).update(may_take_stock=False)
    InventoryPolicy.objects.filter(pk=InventoryPolicy.current().pk).update(mode=InventoryPolicy.MODE_OFF)


def scan(who):
    hex_uid = NEW_CARD if who == "new" else card_hex(MEMBERS.index(who))
    NfcScan.objects.create(uid=hex_uid, event=NfcScan.EVENT_INSERTED)
    print(f"scanned {hex_uid}")


def cleanup():
    inventory_off()
    try:
        with open(MODE_FILE) as f:
            InventoryPolicy.objects.filter(pk=InventoryPolicy.current().pk).update(mode=f.read().strip())
        os.remove(MODE_FILE)
    except FileNotFoundError:
        pass
    uids = [uid(i) for i in range(len(MEMBERS))]
    print("members:", Member.objects.filter(uid__in=uids).delete()[0])
    print("scans:", NfcScan.objects.filter(uid__startswith=CARD_PREFIX).delete()[0])
    print("sorts:", CoffeeSort.objects.filter(name__in=[n for n, _, _ in SORTS], order__isnull=True).delete()[0])
    print("payment method:", PaymentMethod.objects.filter(name=PAYPAL, identifier="coffeefund").delete()[0])


args = os.environ.get("SCRIPT_ARGS", "").split()
{"seed": lambda: seed(), "scan": lambda: scan(args[1]), "cleanup": lambda: cleanup(),
 "inventory": lambda: inventory_on() if args[1] == "on" else inventory_off()}[args[0]]()
