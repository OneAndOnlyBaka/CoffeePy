"""Ordering from the phone (portal.views.order, coffee/turns.py): in line with the kiosk's members, the line
with names, Start once it is the member's turn - booked cup by cup like at the kiosk."""
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee import machine, privacy, slots, turns
from coffee.models import AuditLogEntry, CoffeeSort, MachineDispense, MachineTurn, Member, Order
from dashboard.models import CoffeeMachine


class PortalOrderTestCase(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, "F4:57:52:42:0C:04"
        settings_.save()
        self.kaffee = CoffeeSort.objects.create(name="Kaffee", price=Decimal("0.50"), machine_strokes=1)
        self.tee = CoffeeSort.objects.create(name="Tee", price=Decimal("0.20"), machine_strokes=0, without_machine=True)
        machine.sync_products({"products": [
            {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "brewable": True,
             "recipe": {"strength": {"min": 1, "max": 8, "default": 4}}}]})
        self.connect()
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", privacy_version=privacy.VERSION)
        self.anna = Member.objects.create(uid=7, nickname="Anna X.", privacy_version=privacy.VERSION)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client()
        session = self.client.session
        session["portal_member_uid"] = self.member.uid
        session.save()

    def connect(self, connected=True):
        machine.store({"online": True, "status": "online", "state": {
            "connected": connected, "address": "F4:57:52:42:0C:04", "alerts": [], "blocking": [],
            "progress": {"phase": "idle", "product": 0}, "model": "E6 (EB)"}})

    def post(self, **data):
        return self.client.post(reverse("portal:order"), data, follow=True)

    def test_only_what_the_machine_makes_and_the_slots(self):
        slots.save(self.member, 1, self.kaffee, strength=7, count=2)
        self.assertContains(self.client.get(reverse("portal:index")), reverse("portal:order"))
        page = self.client.get(reverse("portal:order"))
        self.assertContains(page, 'value="slot:1" checked')
        self.assertContains(page, "strength 7")
        self.assertContains(page, "1.00€")
        self.assertContains(page, f'value="{self.kaffee.pk}"')
        self.assertNotContains(page, "Tee")

    def test_in_line_behind_the_kiosk_with_names(self):
        turns.join(self.anna, self.kaffee, None, MachineTurn.SOURCE_KIOSK)
        page = self.post(action="join", choice=str(self.kaffee.pk))
        self.assertContains(page, "You are in line - No. 2")
        self.assertContains(page, "Anna X.")
        self.assertNotContains(page, "Get in line")  # one turn at a time
        self.assertTrue(AuditLogEntry.objects.filter(action="machine.queue_join", details__contains="phone").exists())
        self.assertContains(self.client.get(reverse("portal:order_status")), "Anna X.")

    def test_start_when_it_is_my_turn_and_booked_from_the_phone(self):
        slot = slots.save(self.member, 1, self.kaffee, strength=7, count=2)
        page = self.post(action="join", choice=f"slot:{slot.position}")
        self.assertContains(page, "It's your turn!")
        self.assertContains(page, "s left to start")
        page = self.post(action="start")
        command = self.send.call_args.args[1]
        self.assertEqual((command["source"], command["options"], len(command["request_ids"])),
                         ("phone", {"strength": 7}, 2))
        self.assertContains(page, "Cup 1 of 2")
        first, second = MachineDispense.objects.order_by("cup")
        for cup in (first, second):
            machine.handle_event({"type": "brew_done", "request_id": cup.request_id, "product": 3})
        self.assertContains(self.client.get(reverse("portal:order_status")), "2 × Kaffee booked")
        self.assertEqual(Order.objects.filter(member=self.member).count(), 2)
        self.assertEqual(AuditLogEntry.objects.filter(action="order.create").first().source, "portal")
        self.assertTrue(AuditLogEntry.objects.filter(action="machine.brew", details__contains="phone").exists())

    def test_start_is_refused_unless_it_is_my_turn(self):
        turns.join(self.anna, self.kaffee, None, MachineTurn.SOURCE_KIOSK)
        self.post(action="join", choice=str(self.kaffee.pk))
        self.post(action="start")
        self.assertFalse(MachineDispense.objects.exists())

    def test_giving_up_the_place(self):
        self.post(action="join", choice=str(self.kaffee.pk))
        page = self.post(action="leave")
        self.assertContains(page, "Get in line")
        self.assertEqual(MachineTurn.objects.get().state, MachineTurn.CANCELLED)
        self.assertTrue(AuditLogEntry.objects.filter(action="machine.queue_leave").exists())

    def test_without_the_machine_the_kiosk(self):
        self.connect(connected=False)
        page = self.post(action="join", choice=str(self.kaffee.pk))
        self.assertContains(page, "right now you order at the kiosk")
        self.assertFalse(MachineTurn.objects.exists())

    def test_not_before_the_privacy_notice(self):
        Member.objects.filter(pk=self.member.pk).update(privacy_version=0)
        self.client.post(reverse("portal:order"), {"action": "join", "choice": str(self.kaffee.pk)})
        self.assertFalse(MachineTurn.objects.exists())
