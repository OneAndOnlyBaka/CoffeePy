#!/bin/bash
# A demo copy of CoffeePy in the work dir - its own SQLite, the manual's demo members (user-manual's demo_data.py),
# ranking and pillory on, admin "admin" / demo-video-1 - served on 127.0.0.1:8765, plus a headless Chromium on
# CDP port 9333 for rec.py. The checkout's db.sqlite3 and the test Pi are never touched. Safe to run again.
#
#   setup_app.sh          start (creating the copy the first time)
#   setup_app.sh stop     stop server and browser
#   setup_app.sh fresh    throw the copy away and start over (after code changes the video should show)
set -e
REPO=$(cd "$(dirname "$(readlink -f "$0")")/../../../.." && pwd)
WORK=${VIDEO_WORK:-$REPO/.render/video/work}
P=$REPO/.venv/bin/python
export DJANGO_TEST_SQLITE=1 DJANGO_SECRET_KEY=dev DJANGO_DEBUG=1 DJANGO_LANGUAGE_CODE=en

pids() { ps -eo pid,args | grep -E "[r]unserver 127.0.0.1:8765|[r]emote-debugging-port=9333" | awk '{print $1}'; }
if [ "$1" = stop ] || [ "$1" = fresh ]; then
  pids | xargs -r kill
  [ "$1" = stop ] && exit 0
  rm -rf "$WORK/app"
fi

if [ ! -f "$WORK/app/db.sqlite3" ]; then
  mkdir -p "$WORK/app"
  rsync -a --exclude .venv --exclude .render --exclude .git --exclude db.sqlite3 --exclude data --exclude staticfiles \
    "$REPO/" "$WORK/app/"
  cd "$WORK/app"
  mkdir -p data/storage
  $P manage.py migrate -v0
  SCRIPT_ARGS=seed $P manage.py shell < .claude/skills/user-manual/scripts/demo_data.py
  # What a camera shouldn't see: "card reader not available" (no bridge here), the red backup light (no storage
  # helper - a status.json far in the future reads as a running one), an empty month ranking early in a month,
  # and the demo data's German/English double names.
  echo '{"updated": 9999999999, "devices": []}' > data/storage/status.json
  $P manage.py shell -c "
import random
from datetime import timedelta
from django.contrib.auth.models import User
from django.utils import timezone
from coffee.models import CoffeeSort, Member, NfcReaderStatus, Order, PaymentMethod
from dashboard.models import KioskBoards
KioskBoards.objects.update_or_create(pk=1, defaults=dict(ranking=True, pillory=True))
User.objects.create_superuser('admin', '', 'demo-video-1')
NfcReaderStatus.objects.update_or_create(pk=1, defaults=dict(is_online=True))
CoffeeSort.objects.filter(name__startswith='Kanne').update(name='Coffee jug')
CoffeeSort.objects.get_or_create(name='Latte macchiato', defaults=dict(price='0.60', machine_strokes=1))
PaymentMethod.objects.filter(name__startswith='Überweisung').update(name='Bank transfer')
random.seed(3)
sorts = list(CoffeeSort.objects.all())
start = timezone.now().replace(day=1, hour=7, minute=30)
for name, n in [('Ben K.', 5), ('Clara M.', 3), ('Emma W.', 2), ('Felix H.', 1), ('David S.', 1)]:
    m = Member.objects.get(nickname=name)
    for i in range(n):
        s = random.choice(sorts)
        Order.objects.create(member=m, coffee_sort=s, value=s.price, machine_strokes=s.machine_strokes,
                             created_at=start + timedelta(hours=i * 2), info=f'Ordered [{s.pk}] {s.name}')
"
fi

cd "$WORK/app"
[ -n "$(ps -eo args | grep '[r]unserver 127.0.0.1:8765')" ] ||
  (setsid $P manage.py runserver 127.0.0.1:8765 --noreload > "$WORK/runserver.log" 2>&1 &)
[ -n "$(ps -eo args | grep '[r]emote-debugging-port=9333')" ] ||
  (setsid chromium-browser --headless=new --remote-debugging-port=9333 --user-data-dir="$WORK/chrome" \
     --window-size=1024,600 --hide-scrollbars --autoplay-policy=no-user-gesture-required --no-first-run about:blank \
     > "$WORK/chrome.log" 2>&1 &)
for _ in $(seq 40); do
  curl -sf -o /dev/null http://127.0.0.1:8765/kiosk/ && curl -sf -o /dev/null localhost:9333/json/version && echo ready && exit 0
  sleep 0.5
done
echo "not ready - see $WORK/runserver.log and chrome.log" >&2
exit 1
