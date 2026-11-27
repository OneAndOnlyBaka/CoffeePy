import os
from django.contrib.sessions.backends.db import SessionStore
from coffee import privacy
from coffee.models import CoffeeSort, MachineDispense, MachineTurn, Member, NfcScan, Order
# kiosk-line-test.sh: the kiosk's probe card (P) and a phone member (Q) with a portal session.
PROBES = (0x44332211, 0x55667788)
if os.environ["SCRIPT_ARGS"] == "up":
    kaffee = CoffeeSort.objects.get(name="Kaffee")
    for uid, name in zip(PROBES, ("Probe P.", "Phone Q.")):
        Member.objects.update_or_create(uid=uid, defaults={"nickname": name, "favourite_coffee": kaffee,
                                                          "privacy_version": privacy.VERSION, "favourite_slot": None,
                                                          "hide_standard_sorts": False})
    MachineTurn.objects.filter(member_id__in=PROBES).delete()
    open("/tmp/line_last_dispense", "w").write(str(MachineDispense.objects.order_by("-pk").values_list("pk", flat=True).first() or 0))
    session = SessionStore()
    session["portal_member_uid"] = PROBES[1]
    session.create()
    print("SESSION", session.session_key)
else:
    last = int(open("/tmp/line_last_dispense").read())
    MachineDispense.objects.filter(pk__gt=last).delete()
    Order.objects.filter(member_id__in=PROBES).delete()
    MachineTurn.objects.filter(member_id__in=PROBES).delete()
    from django.contrib.sessions.models import Session
    for s in Session.objects.all():
        if s.get_decoded().get("portal_member_uid") == PROBES[1]:
            s.delete()
    print(Member.objects.filter(uid__in=PROBES).delete(), NfcScan.objects.filter(uid="11223344").delete())
