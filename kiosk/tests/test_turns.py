"""The line for the machine at the kiosk (coffee/turns.py): Order now gets a place in line while someone else
is at the machine, the wait screen says whose number it is (no names), and a card tap of the one called goes
straight to the cup."""
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee import machine, privacy, turns
from coffee.models import (
    AuditLogEntry, CoffeeSort, MachineDispense, MachineProduct, MachineTurn, Member, NfcReaderStatus, NfcScan, Order,
)
from dashboard.models import CoffeeMachine


class KioskTurnsTestCase(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, "F4:57:52:42:0C:04"
        settings_.save()
        self.kaffee = CoffeeSort.objects.create(name="Kaffee", price=Decimal("0.50"), machine_strokes=1)
        machine.sync_products({"products": [
            {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "brewable": True, "recipe": {}},
            {"code": 13, "name": "Hot Water", "strokes": 0, "water_ml": 200, "brewable": False}]})
        MachineProduct.objects.filter(code=13).update(free=True)
        machine.store({"online": True, "status": "online", "state": {
            "connected": True, "address": "F4:57:52:42:0C:04", "alerts": [], "blocking": [],
            "progress": {"phase": "idle", "product": 0}, "model": "E6 (EB)"}})
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.tom = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=self.kaffee,
                                         privacy_version=privacy.VERSION)
        self.anna = Member.objects.create(uid=7, nickname="Anna X.", privacy_version=privacy.VERSION)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def panel(self):
        return self.client.get(reverse("kiosk:panel"))

    def tap(self):
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.panel()

    def anna_on_the_phone(self):
        return turns.join(self.anna, self.kaffee, None, MachineTurn.SOURCE_PHONE)

    def test_the_wait_screen_shows_the_line_without_names(self):
        self.assertContains(self.panel(), "Hot water")
        self.anna_on_the_phone()
        wait = self.panel()
        self.assertContains(wait, "No. 1</b>, it's your turn")
        self.assertNotContains(wait, "Anna")
        self.assertNotContains(wait, "Hot water")  # the line goes first
        self.assertEqual(wait.content, self.panel().content)

    def test_order_now_gets_a_place_in_line(self):
        self.anna_on_the_phone()
        order = self.tap()
        self.assertContains(order, "puts you in line (before you: 1)")
        queued = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(queued, "You are in line - No. 2")
        self.assertContains(queued, "Before you: 1")
        self.assertEqual(queued.content, self.panel().content)
        self.assertTrue(AuditLogEntry.objects.filter(action="machine.queue_join").exists())
        self.assertFalse(self.send.called)

    def test_called_while_on_the_screen_it_goes_on_to_the_cup(self):
        anna = self.anna_on_the_phone()
        self.tap()
        self.client.post(reverse("kiosk:order_confirm"))
        turns.cancel(anna)
        cup = self.panel()
        self.assertContains(cup, "Put your cup under the outlet")
        self.client.post(reverse("kiosk:machine_start"))
        dispense = MachineDispense.objects.get()
        self.assertEqual((dispense.member, dispense.source), (self.tom, MachineDispense.SOURCE_KIOSK))
        machine.handle_event({"type": "brew_done", "request_id": dispense.request_id, "product": 3})
        self.assertContains(self.panel(), "Thank you, Tom X.!")
        self.assertEqual(Order.objects.get().member, self.tom)
        self.assertEqual(MachineTurn.objects.get(member=self.tom).state, MachineTurn.DONE)

    def test_a_card_tap_of_the_one_called_goes_straight_to_the_cup(self):
        anna = self.anna_on_the_phone()
        self.tap()
        self.client.post(reverse("kiosk:order_confirm"))
        self.client.post(reverse("kiosk:machine_cancel"))  # OK: the screen ends, the place stays
        self.assertContains(self.panel(), "Apply your card")
        turns.cancel(anna)
        self.assertContains(self.panel(), "No. 2</b>, it's your turn")
        self.assertContains(self.tap(), "Put your cup under the outlet")

    def test_giving_up_the_place(self):
        self.anna_on_the_phone()
        self.tap()
        self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(self.client.post(reverse("kiosk:turn_leave")), "Apply your card")
        self.assertEqual(MachineTurn.objects.get(member=self.tom).state, MachineTurn.CANCELLED)
        self.assertTrue(AuditLogEntry.objects.filter(action="machine.queue_leave").exists())

    def test_a_cup_screen_given_up_frees_the_machine(self):
        self.tap()
        self.assertContains(self.client.post(reverse("kiosk:order_confirm")), "Put your cup under the outlet")
        anna = self.anna_on_the_phone()
        self.assertEqual(anna.state, MachineTurn.WAITING)
        self.client.post(reverse("kiosk:machine_cancel"))
        anna.refresh_from_db()
        self.assertEqual(anna.state, MachineTurn.CALLED)

    def test_a_turn_that_ran_out_starts_nothing(self):
        self.tap()
        self.client.post(reverse("kiosk:order_confirm"))
        MachineTurn.objects.update(called_at=timezone.now() - timezone.timedelta(seconds=turns.TURN_SECONDS + 1))
        self.assertContains(self.client.post(reverse("kiosk:machine_start")), "Your turn is over")
        self.assertFalse(MachineDispense.objects.exists())

    def test_no_free_product_past_the_line(self):
        self.anna_on_the_phone()
        self.client.post(reverse("kiosk:machine_free"), {"code": "13"})
        self.assertFalse(MachineDispense.objects.exists())
        self.assertNotContains(self.panel(), "Put your cup under the outlet")
