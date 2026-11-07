""""My coffees" at the kiosk (coffee/slots.py): the slots first on the order screen, their brew with the
slot's recipe and cups, the sorts hidden behind them if wanted - and the editor under Settings."""
import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee import machine, privacy, slots
from coffee.models import (
    AuditLogEntry, CoffeeSort, MachineDispense, Member, MemberSlot, NfcReaderStatus, NfcScan, Order,
)
from dashboard.models import CoffeeMachine

RECIPE = {"strength": {"min": 1, "max": 8, "default": 4},
          "water": {"min": 25, "max": 240, "step": 5, "default": 100},
          "temperature": {"options": [1, 2], "default": 2}}


class KioskSlotsTestCase(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, "F4:57:52:42:0C:04"
        settings_.save()
        self.kaffee = CoffeeSort.objects.create(name="Kaffee", price=Decimal("0.50"), machine_strokes=1)
        self.espresso = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.40"), machine_strokes=1)
        machine.sync_products({"products": [
            {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "brewable": True, "recipe": RECIPE},
            {"code": 2, "name": "Espresso", "strokes": 1, "water_ml": 45, "brewable": True, "recipe": {}}]})
        self.connect()
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=self.espresso,
                                            privacy_version=privacy.VERSION)
        self.slot = slots.save(self.member, 1, self.kaffee, strength=7, water_ml=200, count=2)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def connect(self, connected=True):
        machine.store({"online": True, "status": "online", "state": {
            "connected": connected, "address": "F4:57:52:42:0C:04", "alerts": [], "blocking": [],
            "progress": {"phase": "idle", "product": 0}, "model": "E6 (EB)"}})

    def panel(self):
        return self.client.get(reverse("kiosk:panel"))

    def log_in(self):
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.panel()

    def test_the_slots_come_first_and_the_favourite_stays_preselected(self):
        order = self.log_in()
        self.assertContains(order, "My coffees")
        self.assertContains(order, 'value="slot:1"')
        self.assertContains(order, "strength 7 · 200 ml · 2×")
        self.assertContains(order, "1.00€")  # the sort's price, twice
        self.assertRegex(order.content.decode(), rf'value="{self.espresso.pk}"\s+checked')
        self.assertEqual(order.content, self.panel().content)

    def test_a_slot_brews_its_recipe_cup_after_cup(self):
        self.log_in()
        self.client.post(reverse("kiosk:order_select"), {"coffee_id": "slot:1"})
        cup = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(cup, "2 × Kaffee in a row")
        self.client.post(reverse("kiosk:machine_start"))
        command = self.send.call_args.args[1]
        self.assertEqual((command["product"], command["options"], len(command["request_ids"])),
                         (3, {"strength": 7, "water_ml": 200}, 2))
        first, second = MachineDispense.objects.order_by("cup")
        machine.handle_event({"type": "brew_done", "request_id": first.request_id, "product": 3})
        machine.store({"online": True, "status": "online", "state": {
            "connected": True, "address": "F4:57:52:42:0C:04", "alerts": [], "blocking": [],
            "progress": {"phase": "dispensing", "product": 3}, "model": "E6 (EB)"}})
        brewing = self.panel()
        self.assertContains(brewing, "Cup 2 of 2")
        self.assertEqual(brewing.content, self.panel().content)
        machine.handle_event({"type": "brew_done", "request_id": second.request_id, "product": 3})
        self.assertContains(self.panel(), "Thank you, Tom X.!")
        self.assertEqual(Order.objects.filter(member=self.member, coffee_sort=self.kaffee).count(), 2)

    def test_a_cup_that_fails_says_how_many_were_made(self):
        self.member.favourite_slot = 1
        self.member.save()
        self.log_in()
        self.client.post(reverse("kiosk:order_confirm"))  # the preselected slot
        self.client.post(reverse("kiosk:machine_start"))
        first, second = MachineDispense.objects.order_by("cup")
        machine.handle_event({"type": "brew_done", "request_id": first.request_id, "product": 3})
        machine.handle_event({"type": "brew_failed", "request_id": second.request_id, "product": 3,
                              "reason": "previous cup failed: blocked"})
        self.assertContains(self.panel(), "1 of 2 made and booked")
        self.assertEqual(Order.objects.count(), 1)

    def test_the_preselected_slot_is_checked_out_by_itself(self):
        self.member.favourite_slot = 1
        self.member.save()
        self.connect(connected=False)  # the tally alone: two orders at once
        self.assertContains(self.log_in(), 'value="slot:1"\n           checked')
        with mock.patch("time.time", return_value=time.time() + 25):
            self.assertContains(self.panel(), "Thank you")
        self.assertEqual(Order.objects.filter(coffee_sort=self.kaffee).count(), 2)

    def test_hidden_sorts_are_a_page_on(self):
        self.member.hide_standard_sorts = True
        self.member.save()
        order = self.log_in().content.decode()
        self.assertIn("Other sorts ›", order)
        first_page = order[order.index('data-page="0"'):order.index('data-page="1"')]
        self.assertIn('value="slot:1"', first_page)
        self.assertNotIn(f'value="{self.kaffee.pk}"', first_page)
        self.assertIn(f'value="{self.espresso.pk}"', order)  # one page on
        # What is preselected - and checked out after 20 s - is in sight: the first slot, not the favourite.
        self.assertRegex(order, r'class="sort-page current" data-page="0"')
        self.assertRegex(order, r'value="slot:1"\s+checked')

    def test_the_editor_saves_within_the_recipe(self):
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        self.log_in()
        editor = self.client.post(reverse("kiosk:slots_open"))
        self.assertContains(editor, 'name="s1-strength" value="7"')
        self.assertContains(editor, 'id="slot-recipes"')
        self.assertEqual(editor.content, self.panel().content)
        saved = self.client.post(reverse("kiosk:slots_save"), {
            "s1-sort": self.kaffee.pk, "s1-strength": "9", "s1-water": "100", "s1-temperature": "2", "s1-count": "3",
            "s2-sort": self.espresso.pk, "s2-count": "1", "s3-sort": "", "favourite": "2", "hide_standard_sorts": "on"})
        self.assertContains(saved, "Saved")
        slot = MemberSlot.objects.get(member=self.member, position=1)
        self.assertEqual((slot.strength, slot.water_ml, slot.temperature, slot.count), (8, None, None, 3))
        self.member.refresh_from_db()
        self.assertEqual((self.member.favourite_slot, self.member.hide_standard_sorts), (2, True))
        self.assertTrue(AuditLogEntry.objects.filter(action="member.slots_update").exists())
        self.assertContains(self.client.post(reverse("kiosk:slots_close")), "Favourite coffee")

    def test_no_slots_for_a_session_waiting_for_the_privacy_notice(self):
        Member.objects.filter(pk=self.member.pk).update(privacy_version=0)
        self.client.get(reverse("kiosk:nav", args=["settings"]))
        self.log_in()
        self.client.post(reverse("kiosk:slots_open"))
        self.client.post(reverse("kiosk:slots_save"), {"s1-sort": "", "s2-sort": "", "s3-sort": ""})
        self.assertTrue(MemberSlot.objects.filter(member=self.member).exists())
