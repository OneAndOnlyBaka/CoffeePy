"""The line for the machine (coffee/turns.py): one turn at a time, called when the machine is free, a minute
to press Start, then the next - from the kiosk and the phone alike."""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from coffee import machine, privacy, slots, turns
from coffee.models import CoffeeSort, MachineDispense, MachineTurn, Member, Order
from dashboard.models import CoffeeMachine


def connect(connected=True):
    machine.store({"online": True, "status": "online", "state": {
        "connected": connected, "address": "F4:57:52:42:0C:04", "alerts": [], "blocking": [],
        "progress": {"phase": "idle", "product": 0}, "model": "E6 (EB)"}})


class TurnsTestCase(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, "F4:57:52:42:0C:04"
        settings_.save()
        self.kaffee = CoffeeSort.objects.create(name="Kaffee", price=Decimal("0.50"), machine_strokes=1)
        machine.sync_products({"products": [
            {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "brewable": True, "recipe": {}}]})
        connect()
        self.anna = Member.objects.create(uid=1, nickname="Anna X.", privacy_version=privacy.VERSION)
        self.ben = Member.objects.create(uid=2, nickname="Ben X.", privacy_version=privacy.VERSION)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def join(self, member, source=MachineTurn.SOURCE_PHONE, slot=None):
        return turns.join(member, self.kaffee, slot, source)

    def test_the_first_is_called_the_next_waits(self):
        anna, ben = self.join(self.anna), self.join(self.ben, MachineTurn.SOURCE_KIOSK)
        self.assertEqual((anna.state, ben.state), (MachineTurn.CALLED, MachineTurn.WAITING))
        self.assertEqual((anna.number, ben.number), (1, 2))
        self.assertEqual(turns.ahead(ben), [anna])
        self.assertEqual(turns.ahead(anna), [])

    def test_the_next_is_called_once_the_brew_is_over(self):
        anna, ben = self.join(self.anna), self.join(self.ben)
        dispense = turns.start(anna, MachineDispense.SOURCE_PHONE)
        self.assertEqual(self.send.call_args.args[1]["source"], "phone")
        turns.advance()
        ben.refresh_from_db()
        self.assertEqual(ben.state, MachineTurn.WAITING)  # running
        machine.handle_event({"type": "brew_done", "request_id": dispense.request_id, "product": 3})
        turns.advance()
        anna.refresh_from_db()
        ben.refresh_from_db()
        self.assertEqual((anna.state, ben.state), (MachineTurn.DONE, MachineTurn.CALLED))
        self.assertEqual(Order.objects.get().member, self.anna)

    def test_a_minute_to_start_then_the_next(self):
        anna, ben = self.join(self.anna), self.join(self.ben)
        turns.advance(timezone.now() + timezone.timedelta(seconds=turns.TURN_SECONDS + 1))
        anna.refresh_from_db()
        ben.refresh_from_db()
        self.assertEqual((anna.state, ben.state), (MachineTurn.EXPIRED, MachineTurn.CALLED))
        self.assertIsNone(turns.start(anna, MachineDispense.SOURCE_PHONE))
        self.assertFalse(MachineDispense.objects.exists())

    def test_a_claim_restarts_the_minute(self):
        anna = self.join(self.anna)
        later = timezone.now() + timezone.timedelta(seconds=turns.TURN_SECONDS - 5)
        turns.claim(anna, later)
        turns.advance(later + timezone.timedelta(seconds=30))
        anna.refresh_from_db()
        self.assertEqual(anna.state, MachineTurn.CALLED)

    def test_nobody_is_called_while_the_machine_is_busy_or_away(self):
        MachineDispense.objects.create(code=3, source=MachineDispense.SOURCE_MANUAL, state=MachineDispense.STARTED)
        self.assertEqual(self.join(self.anna).state, MachineTurn.WAITING)
        MachineDispense.objects.update(state=MachineDispense.DONE)
        connect(connected=False)
        self.assertEqual(turns.advance()[0].state, MachineTurn.WAITING)
        connect()
        self.assertEqual(turns.advance()[0].state, MachineTurn.CALLED)

    def test_one_turn_a_member_and_a_new_choice_replaces_the_old(self):
        self.join(self.ben)
        first = self.join(self.anna)
        slot = slots.save(self.anna, 1, self.kaffee, count=2)
        again = self.join(self.anna, slot=slot)
        self.assertEqual((again.pk, again.count, again.slot_position), (first.pk, 2, 1))
        self.assertEqual(MachineTurn.objects.count(), 2)

    def test_cups_in_a_row_hold_the_turn_until_the_last(self):
        slot = slots.save(self.anna, 1, self.kaffee, count=2)
        anna, ben = self.join(self.anna, slot=slot), self.join(self.ben)
        first = turns.start(anna, MachineDispense.SOURCE_KIOSK)
        second = MachineDispense.objects.get(chain=first.chain, cup=2)
        machine.handle_event({"type": "brew_done", "request_id": first.request_id, "product": 3})
        turns.advance()
        ben.refresh_from_db()
        self.assertEqual(ben.state, MachineTurn.WAITING)
        machine.handle_event({"type": "brew_failed", "request_id": second.request_id, "product": 3, "reason": "blocked"})
        turns.advance()
        anna.refresh_from_db()
        self.assertEqual(anna.state, MachineTurn.DONE)  # one cup was made and booked
        self.assertEqual(anna.reason, "blocked")

    def test_leaving_calls_the_next(self):
        anna, ben = self.join(self.anna), self.join(self.ben)
        self.assertTrue(turns.cancel(anna))
        ben.refresh_from_db()
        self.assertEqual(ben.state, MachineTurn.CALLED)
        turns.start(ben, MachineDispense.SOURCE_PHONE)
        self.assertFalse(turns.cancel(ben))  # a brew that runs runs to its end

    def test_an_empty_line_costs_one_query(self):
        with self.assertNumQueries(1):
            self.assertEqual(turns.advance(), [])

    def test_finished_turns_go_after_a_day(self):
        anna = self.join(self.anna)
        turns.cancel(anna)
        turns.housekeeping(timezone.now() + timezone.timedelta(seconds=turns.KEEP_SECONDS + 1))
        self.assertFalse(MachineTurn.objects.exists())
