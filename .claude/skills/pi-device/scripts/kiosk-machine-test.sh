#!/bin/bash
# Runs kiosk_machine_test.py on the Pi: the coffee machine's kiosk screens (guided brew, free product,
# blocked machine, the ring's modal, the runaways tab) with real touches, against the bridge's simulator -
# start it first: sudo MACHINE_SIMULATE=true docker compose up -d machine-bridge, and a machine set up in
# the dashboard. Same setup and cleanup as kiosk-ui-test.sh, plus the runaways board and the dispenses.
#
#   kiosk-machine-test.sh [ROUNDS]      default 4 rounds
#
# Synthetic card scans on the device - ask the user first (SKILL.md, "Testing the kiosk's UI").
# Restarts the kiosk session twice (debug port on/off): nobody should be at the touchscreen.
set -euo pipefail
DIR="$(dirname "$0")"
ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
. "$ROOT/.device/pi.env"
rounds="${1:-4}"
variant="${2:-}"   # plain|cup: an A/B of the cup screen instead of the rounds (kiosk_machine_test.py)
sorts=3
setup="$(mktemp)"; cleanup="$(mktemp)"; trap 'rm -f "$setup" "$cleanup"' EXIT
cat > "$setup" <<'PY'
from decimal import Decimal
from coffee.models import CoffeeSort, Member, PaymentMethod
from dashboard.models import KioskBoards
import os
for name, price, strokes in (("Kaffee", "0.30", 1), ("Espresso", "0.40", 1), ("Kanne", "0.60", 2)):
    CoffeeSort.objects.get_or_create(name=name, defaults={"price": Decimal(price), "machine_strokes": strokes})
extra = ["Cappuccino", "Latte Macchiato", "Doppelter Espresso", "Heiße Schokolade", "Flat White", "Lungo",
         "Ristretto", "Americano", "Chai Latte", "Grüner Tee", "Kakao groß", "Eiskaffee", "Pfefferminztee"]
made = []
for name in extra:
    if CoffeeSort.objects.count() >= int(os.environ.get("SCRIPT_ARGS") or 3):
        break
    if CoffeeSort.objects.get_or_create(name=name, defaults={"price": Decimal("0.50"), "machine_strokes": 1})[1]:
        made.append(name)
open("/tmp/kiosk_ui_sorts", "w").write("\n".join(made))
print("sorts:", CoffeeSort.objects.count())
Member.objects.update_or_create(uid=0x44332211, defaults={"nickname": "Probe P.", "favourite_coffee": CoffeeSort.objects.get(name="Kaffee")})
PaymentMethod.objects.get_or_create(name="PayPal Test", defaults={"provider": "paypal", "identifier": "kaffeetest"})
boards = KioskBoards.objects.get_or_create(pk=1)[0]
open("/tmp/kiosk_ui_boards", "w").write(f"{int(boards.ranking)} {int(boards.pillory)}")
open("/tmp/kiosk_ui_runaways", "w").write(str(int(boards.runaways)))
boards.ranking = boards.pillory = boards.runaways = True
boards.save()
from coffee.models import MachineDispense
open("/tmp/kiosk_ui_dispense", "w").write(str(MachineDispense.objects.order_by("-pk").values_list("pk", flat=True).first() or 0))
PY
cat > "$cleanup" <<'PY'
from coffee.models import MachineDispense, MachineIncident, Member, NfcScan, Order, Payment, PaymentMethod
from dashboard.models import KioskBoards
try:
    last = int(open("/tmp/kiosk_ui_dispense").read())
    MachineIncident.objects.filter(dispense__pk__gt=last).delete()
    MachineDispense.objects.filter(pk__gt=last).delete()
    KioskBoards.objects.filter(pk=1).update(runaways=open("/tmp/kiosk_ui_runaways").read() == "1")
except FileNotFoundError:
    pass
MachineIncident.objects.filter(member_id=0x44332211).delete()
Order.objects.filter(member_id=0x44332211).delete(); Payment.objects.filter(member_id=0x44332211).delete()
Member.objects.filter(uid=0x44332211).delete(); PaymentMethod.objects.filter(name="PayPal Test").delete()
NfcScan.objects.filter(uid="11223344").delete()
try:
    from coffee.models import CoffeeSort
    CoffeeSort.objects.filter(name__in=open("/tmp/kiosk_ui_sorts").read().split("\n")).delete()
except FileNotFoundError:
    pass
try:
    ranking, pillory = open("/tmp/kiosk_ui_boards").read().split()
    KioskBoards.objects.filter(pk=1).update(ranking=ranking == "1", pillory=pillory == "1")
except FileNotFoundError:
    pass
print("test data removed")
PY
"$DIR/django.sh" "$setup" "$sorts" | tail -2
"$DIR/kiosk-browser.sh" on | tail -1
scp -q "$DIR/cdp.py" "$DIR/kiosk_machine_test.py" "$PI_USER@$PI_HOST:/tmp/"
"$DIR/pi.sh" "python3 -I /tmp/kiosk_machine_test.py $rounds $variant; python3 -I /tmp/cdp.py eval 'document.querySelector(\"a.nav-btn[data-mode=order]\").click(); 1' >/dev/null" || true
"$DIR/django.sh" "$cleanup" | tail -1
"$DIR/kiosk-browser.sh" off | tail -1
