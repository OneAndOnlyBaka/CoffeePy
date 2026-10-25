"""Server-side time of every dashboard, portal and kiosk page - run through django.sh,
ideally after `perf_data.py seed` (an empty database makes everything look fast).

Uses Django's test client inside the web container, so the numbers are Django + Postgres
alone, without network or browser. The kiosk rows matter most: those screens are
re-rendered on every ~500ms poll for as long as the kiosk shows them.

Logs the kiosk client in by writing its *own* session, never through NfcScan rows - see
perf_data.py for what a synthetic scan does to the live touchscreen.
"""
import statistics
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from coffee.models import Member, MemberDevice, MemberLinkToken

PERF_CARD_UID = 0x44332211


def measure(c, label, url, n=5):
    r = c.get(url)
    times = []
    for _ in range(n):
        t = time.perf_counter()
        c.get(url)
        times.append((time.perf_counter() - t) * 1000)
    print(f"  {label:28} {r.status_code}  {len(r.content) // 1024:5d} KB  median {statistics.median(times):6.0f} ms")


member = Member.objects.filter(uid=PERF_CARD_UID).first() or Member.objects.order_by("uid").first()
print(f"test member: {member} ({member.orders.count()} orders)")

print("== dashboard")
d = Client(HTTP_HOST="localhost")
d.force_login(get_user_model().objects.filter(is_superuser=True).first())
for label, url in [
    ("index", "/dashboard/"), ("statistics", "/dashboard/statistics/"), ("pillory", "/dashboard/pillory/"),
    ("members", "/dashboard/members/"), ("member edit", f"/dashboard/members/{member.uid}/edit/"),
    ("members merge", "/dashboard/members/merge/"), ("coffee sorts", "/dashboard/coffee-sorts/"),
    ("payment methods", "/dashboard/payment-methods/"), ("system", "/dashboard/system/"),
    ("backup", "/dashboard/backup/"), ("update", "/dashboard/update/"), ("audit log", "/dashboard/audit-log/"),
    ("export orders.csv", "/dashboard/export/orders.csv"), ("export payments.csv", "/dashboard/export/payments.csv"),
]:
    measure(d, label, url, n=3)

print("== portal")
p = Client(HTTP_HOST="localhost")
token = MemberLinkToken.objects.create(member=member, expires_at=timezone.now() + timedelta(minutes=5))
p.get(f"/portal/link/{token.token}/")
for label, url in [("index", "/portal/"), ("stats", "/portal/stats/"), ("pay", "/portal/pay/"),
                   ("cards", "/portal/cards/"), ("devices", "/portal/devices/")]:
    measure(p, label, url)
# The link above registered this client as one of the member's phones - without seed data
# that member is a real person, who would find a stranger's device in their list.
MemberDevice.objects.filter(session_key=p.session.session_key).delete()
p.session.flush()

print("== kiosk panel (polled every 500ms)")
k = Client(HTTP_HOST="localhost")
k.get("/kiosk/")
for mode in ("order", "ranking"):
    k.get(f"/kiosk/nav/{mode}/")
    measure(k, f"idle {mode}", "/kiosk/panel/", n=10)
for view in ("month", "pillory"):
    s = k.session
    s["ranking_view"] = view
    s.save()
    measure(k, f"idle ranking/{view}", "/kiosk/panel/", n=10)
for mode in ("order", "info", "settings"):
    k.get(f"/kiosk/nav/{mode}/")
    s = k.session  # what kiosk.views._start_session would store after a card tap
    s.update({"mode": mode, "logged_in": True, "uid": member.uid, "is_new_user": False,
              "deadline": time.time() + 3600, "order_coffee_id": None, "order_auto_checkout": False})
    s.save()
    measure(k, f"logged in {mode}", "/kiosk/panel/", n=10)
k.get("/kiosk/nav/order/")  # logs the client out again
