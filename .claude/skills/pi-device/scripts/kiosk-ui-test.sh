#!/bin/bash
# Runs kiosk_ui_test.py on the Pi: test member and payment method in, debug port on, ROUNDS rounds
# of every kiosk screen with real touches, then everything out again and the debug port off.
#
#   kiosk-ui-test.sh [ROUNDS]       default 4; output also in the scratchpad if you tee it
#
# Synthetic card scans on the device - ask the user first (SKILL.md, "Testing the kiosk's UI").
# Restarts the kiosk session twice (debug port on/off): nobody should be at the touchscreen.
set -euo pipefail
DIR="$(dirname "$0")"
ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
. "$ROOT/.device/pi.env"
rounds="${1:-4}"
setup="$(mktemp)"; cleanup="$(mktemp)"; trap 'rm -f "$setup" "$cleanup"' EXIT
cat > "$setup" <<'PY'
from decimal import Decimal
from coffee.models import CoffeeSort, Member, PaymentMethod
from dashboard.models import KioskBoards
for name, price, strokes in (("Kaffee", "0.30", 1), ("Espresso", "0.40", 1), ("Kanne", "0.60", 2)):
    CoffeeSort.objects.get_or_create(name=name, defaults={"price": Decimal(price), "machine_strokes": strokes})
Member.objects.update_or_create(uid=0x44332211, defaults={"nickname": "Probe P.", "favourite_coffee": CoffeeSort.objects.get(name="Kaffee")})
PaymentMethod.objects.get_or_create(name="PayPal Test", defaults={"provider": "paypal", "identifier": "kaffeetest"})
boards = KioskBoards.objects.get_or_create(pk=1)[0]
open("/tmp/kiosk_ui_boards", "w").write(f"{int(boards.ranking)} {int(boards.pillory)}")
boards.ranking = boards.pillory = True
boards.save()
PY
cat > "$cleanup" <<'PY'
from coffee.models import Member, NfcScan, Order, Payment, PaymentMethod
from dashboard.models import KioskBoards
Order.objects.filter(member_id=0x44332211).delete(); Payment.objects.filter(member_id=0x44332211).delete()
Member.objects.filter(uid=0x44332211).delete(); PaymentMethod.objects.filter(name="PayPal Test").delete()
NfcScan.objects.filter(uid="11223344").delete()
try:
    ranking, pillory = open("/tmp/kiosk_ui_boards").read().split()
    KioskBoards.objects.filter(pk=1).update(ranking=ranking == "1", pillory=pillory == "1")
except FileNotFoundError:
    pass
print("test data removed")
PY
"$DIR/django.sh" "$setup" | tail -1
"$DIR/kiosk-browser.sh" on | tail -1
scp -q "$DIR/cdp.py" "$DIR/kiosk_ui_test.py" "$PI_USER@$PI_HOST:/tmp/"
"$DIR/pi.sh" "python3 -I /tmp/kiosk_ui_test.py $rounds; python3 -I /tmp/cdp.py eval 'document.querySelector(\"a.nav-btn[data-mode=order]\").click(); 1' >/dev/null" || true
"$DIR/django.sh" "$cleanup" | tail -1
"$DIR/kiosk-browser.sh" off | tail -1
