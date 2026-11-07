"""The kiosk with a coffee machine: the guided brew, booked once the machine is done, free products
without a card, the machine's ring and its modal - and the tally as it always was without one."""
import time
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from coffee import machine
from coffee.models import (
    CoffeeSort, MachineDispense, MachineIncident, MachineProduct, Member, NfcReaderStatus, NfcScan, Order,
)
from dashboard.models import CoffeeMachine, KioskBoards

ADDRESS = "F4:57:52:42:0C:04"


class MachineOrderTestCase(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, ADDRESS
        settings_.save()
        self.espresso = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.40"), machine_strokes=1)
        self.biscuit = CoffeeSort.objects.create(name="Biscuit", price=Decimal("0.20"), machine_strokes=0,
                                                 without_machine=True)
        machine.sync_products({"products": [
            {"code": 2, "name": "Espresso", "strokes": 1, "water_ml": 45, "brewable": True},
            {"code": 13, "name": "Hot Water", "strokes": 1, "brewable": False}]})
        hot = MachineProduct.objects.get(code=13)
        hot.water_ml = 200
        hot.save()
        self.connect()
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=self.espresso)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def connect(self, connected=True, alerts=(), blocking=(), phase="idle"):
        machine.store({"online": True, "status": "online", "state": {
            "connected": connected, "address": ADDRESS, "alerts": list(alerts), "blocking": list(blocking),
            "progress": {"phase": phase, "product": 0}, "model": "E6 (EB)"}})

    def panel(self):
        return self.client.get(reverse("kiosk:panel"))

    def log_in(self):
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.panel()

    def to_cup(self):
        self.log_in()
        return self.client.post(reverse("kiosk:order_confirm"))

    def start(self):
        self.to_cup()
        self.client.post(reverse("kiosk:machine_start"))
        return MachineDispense.objects.get()

    def done(self, dispense):
        machine.handle_event({"type": "brew_done", "request_id": dispense.request_id, "product": 2, "water_ml": 45})

    def test_put_the_cup_then_start_then_thanks(self):
        response = self.to_cup()
        self.assertContains(response, "Put your cup under the outlet")
        # Not "brew-cup": that is the cup's own class, and the card once shrank to its 200px.
        self.assertContains(response, 'class="panel-card brew-card stage-cup"')
        self.assertFalse(Order.objects.exists())  # nothing booked yet
        response = self.client.post(reverse("kiosk:machine_start"))
        dispense = MachineDispense.objects.get()
        self.assertEqual((dispense.member, dispense.coffee_sort, dispense.source), (self.member, self.espresso, "app"))
        self.assertEqual(self.send.call_args.args[1]["product"], 2)
        self.assertContains(response, "The machine is getting ready")
        self.connect(phase="dispensing")
        pouring = self.panel()
        self.assertContains(pouring, "Your Espresso is on its way")
        self.assertContains(pouring, "wait_order.gif")  # the wait screen's cup fills, heating had the still
        self.done(dispense)
        self.assertContains(self.panel(), "Thank you, Tom X.!")
        self.assertEqual(Order.objects.get().member, self.member)

    def test_every_stage_is_the_same_from_poll_to_poll(self):
        first = self.to_cup().content
        later = time.time() + 3
        with mock.patch("time.time", return_value=later):
            self.assertEqual(first, self.panel().content)
        self.assertNotIn(b"csrfmiddlewaretoken", first)
        self.client.post(reverse("kiosk:machine_start"))
        self.connect(phase="dispensing")
        brewing = self.panel().content
        self.assertEqual(brewing, self.panel().content)

    def test_automatic_checkout_goes_to_the_cup_too(self):
        self.log_in()
        with mock.patch("time.time", return_value=time.time() + 25):
            self.assertContains(self.panel(), "Put your cup under the outlet")
        self.assertFalse(Order.objects.exists())

    def test_a_blocked_machine_shows_what_to_do(self):
        self.connect(alerts=["fill_water"], blocking=["fill_water"])
        response = self.to_cup()
        self.assertContains(response, "Fill the water tank")
        self.assertContains(response, " disabled>")
        self.client.post(reverse("kiosk:machine_start"))
        self.assertFalse(MachineDispense.objects.exists())

    def test_a_failed_brew_books_nothing(self):
        dispense = self.start()
        machine.handle_event({"type": "brew_failed", "request_id": dispense.request_id, "reason": "busy"})
        response = self.panel()
        self.assertContains(response, "Nothing was booked")
        self.assertContains(response, "busy with another product")
        self.assertFalse(Order.objects.exists())
        with mock.patch("time.time", return_value=time.time() + 20):
            self.assertContains(self.panel(), "Apply your card")

    def test_the_cup_screen_gives_up_and_can_be_cancelled(self):
        self.to_cup()
        with mock.patch("time.time", return_value=time.time() + 40):
            self.assertContains(self.panel(), "Apply your card")
        self.to_cup()
        self.assertContains(self.client.post(reverse("kiosk:machine_cancel")), "Apply your card")
        self.assertFalse(Order.objects.exists() or MachineDispense.objects.exists())

    def test_leaving_the_screen_while_it_brews_still_books(self):
        dispense = self.start()
        self.client.get(reverse("kiosk:nav", args=["info"]))
        self.done(dispense)
        self.assertEqual(Order.objects.count(), 1)

    def test_without_the_machine_the_tally_as_always(self):
        self.connect(connected=False)
        response = self.to_cup()
        self.assertContains(response, "Thank you")
        self.assertEqual(Order.objects.count(), 1)
        self.connect()
        self.member.favourite_coffee = self.biscuit  # not made by the machine
        self.member.save()
        self.to_cup()
        self.assertEqual(Order.objects.count(), 2)

    def test_free_products_without_a_card(self):
        wait = self.panel()
        self.assertContains(wait, "<b>Hot water</b><small>free · no card needed</small>")
        self.assertEqual(wait.content, self.panel().content)
        response = self.client.post(reverse("kiosk:machine_free"), {"code": "13"})
        self.assertContains(response, "Free - nothing is booked.")
        self.client.post(reverse("kiosk:machine_start"))
        dispense = MachineDispense.objects.get()
        self.assertEqual((dispense.source, dispense.member), ("free", None))
        self.assertEqual(self.send.call_args.args[1]["options"], {"water_ml": 200})
        self.connect(phase="dispensing")
        # The product's label, not the machine's own name ("Hot Water"; in German it said "Milk Foam").
        self.assertContains(self.panel(), "Your Hot water is on its way")
        machine.handle_event({"type": "brew_done", "request_id": dispense.request_id, "product": 13})
        self.assertContains(self.panel(), "Enjoy!")
        self.assertFalse(Order.objects.exists())
        self.client.post(reverse("kiosk:machine_free"), {"code": "2"})  # not a free one
        self.assertNotContains(self.panel(), "Put your cup")

    def test_many_free_products_share_one_caption_and_stop_at_one_row(self):
        for code, name in ((20, "Milk"), (21, "Milk Foam"), (22, "Hot Milk"), (23, "Tea Water"), (24, "Steam")):
            MachineProduct.objects.create(code=code, name=name, strokes=1, present=True, free=True, water_ml=100)
        wait = self.panel()
        self.assertContains(wait, "Free, no card needed:", count=1)
        self.assertContains(wait, 'class="free-products many"')
        self.assertNotContains(wait, "free · no card needed")  # said once above, not in every button
        # Six free, five fit - in each language's copy of the wait screen (panel.html).
        self.assertContains(wait, 'class="free-btn"', count=machine.FREE_ON_KIOSK * len(settings.LANGUAGES))

    def test_obsolete_sorts_are_not_offered(self):
        CoffeeSort.objects.create(name="Latte", price=Decimal("0.80"), machine_strokes=1, is_obsolete=True)
        response = self.log_in()
        self.assertContains(response, "Espresso")
        self.assertNotContains(response, "Latte")

    def test_the_ring_and_its_modal(self):
        self.assertContains(self.panel(), "machine-btn machine-ok")
        self.connect(alerts=["no_beans"])
        self.assertContains(self.panel(), "machine-btn machine-warn")
        self.connect(alerts=["fill_water"], blocking=["fill_water"])
        self.assertContains(self.panel(), "machine-btn machine-blocked")
        self.connect(connected=False)
        self.assertContains(self.panel(), "machine-btn machine-off")
        self.connect(alerts=["fill_water"], blocking=["fill_water"])
        popup = self.client.get(reverse("kiosk:machine_popup"))
        self.assertContains(popup, "E6 (EB)")
        self.assertContains(popup, "Fill the water tank")
        self.assertContains(popup, "Supplies")
        self.assertContains(self.client.post(reverse("kiosk:machine_refill")), "Beans refilled")
        CoffeeMachine.objects.filter(pk=1).update(driver="none")
        self.assertNotContains(self.panel(), "machine-btn")

    def test_the_thank_you_waits_for_what_the_machine_asks(self):
        dispense = self.start()
        self.done(dispense)
        MachineIncident.objects.create(member=self.member, alert="empty_grounds", dispense=dispense)
        response = self.panel()
        self.assertContains(response, "Empty the grounds container")
        with mock.patch("time.time", return_value=time.time() + 30):
            self.assertContains(self.panel(), "Empty the grounds container")  # past the usual 20s
        machine.track_alerts(["empty_grounds"], [])
        with mock.patch("time.time", return_value=time.time() + 31):
            self.assertContains(self.panel(), "thanks, hero!")

    def test_the_ran_off_board(self):
        KioskBoards.objects.update_or_create(pk=1, defaults={"runaways": True})
        MachineIncident.objects.create(member=self.member, alert="fill_water", outcome="left")
        self.client.get(reverse("kiosk:nav", args=["ranking"]))
        response = self.panel()
        self.assertContains(response, "Ran off")
        self.assertContains(response, "ran off 1×")
        self.assertEqual(response.content, self.panel().content)
        CoffeeMachine.objects.filter(pk=1).update(driver="none")
        self.assertEqual(KioskBoards.current().kiosk_views(), [])
