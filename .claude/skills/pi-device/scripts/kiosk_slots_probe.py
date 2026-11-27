import os
from coffee import privacy
from coffee.models import CoffeeSort, MachineDispense, Member, MemberSlot, NfcScan, Order
if os.environ["SCRIPT_ARGS"] == "up":
    Member.objects.update_or_create(uid=0x44332211, defaults={"nickname": "Probe P.", "favourite_coffee": CoffeeSort.objects.get(name="Kaffee"), "privacy_version": privacy.VERSION, "hide_standard_sorts": False, "favourite_slot": None})
    MemberSlot.objects.filter(member_id=0x44332211).delete()
    open("/tmp/slots_last_dispense", "w").write(str(MachineDispense.objects.order_by("-pk").values_list("pk", flat=True).first() or 0))
    print("up", list(CoffeeSort.offered().values_list("name", flat=True)))
else:
    last = int(open("/tmp/slots_last_dispense").read())
    MachineDispense.objects.filter(pk__gt=last).delete()
    Order.objects.filter(member_id=0x44332211).delete()
    print(Member.objects.filter(uid=0x44332211).delete(), NfcScan.objects.filter(uid="11223344").delete())
