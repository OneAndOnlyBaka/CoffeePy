"""A member's "my coffees" (coffee/slots.py) and the cups of one slot in a row (coffee/machine.py):
kept within what the machine's product allows, booked cup by cup at the sort's price."""
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from coffee import machine, slots
from coffee.models import CoffeeSort, MachineDispense, MachineProduct, Member, MemberSlot, Order
from dashboard.models import CoffeeMachine

COFFEE_RECIPE = {"strength": {"min": 1, "max": 8, "default": 4},
                 "water": {"min": 25, "max": 240, "step": 5, "default": 100},
                 "temperature": {"options": [1, 2], "default": 2}}


class SlotsTestCase(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, "F4:57:52:42:0C:04"
        settings_.save()
        self.kaffee = CoffeeSort.objects.create(name="Kaffee", price=Decimal("0.50"), machine_strokes=1)
        self.tee = CoffeeSort.objects.create(name="Tee", price=Decimal("0.20"), machine_strokes=0, without_machine=True)
        machine.sync_products({"products": [
            {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "brewable": True, "recipe": COFFEE_RECIPE}]})
        self.product = MachineProduct.objects.get(code=3)
        self.member = Member.objects.create(uid=1, nickname="Anna X.")

    def test_the_recipe_comes_with_the_products(self):
        self.assertEqual(self.product.recipe, COFFEE_RECIPE)
        self.assertEqual(self.product.coffee_sort, self.kaffee)

    def test_values_are_kept_within_the_recipe(self):
        self.assertEqual(slots.clean(self.kaffee, strength=12, water_ml=203, temperature=1, count=9),
                         {"strength": 8, "water_ml": 205, "temperature": 1, "count": MemberSlot.MAX_COUNT})
        # the machine's default is stored as "default", so a slot follows it
        self.assertEqual(slots.clean(self.kaffee, strength=4, water_ml=100, temperature=2),
                         {"strength": None, "water_ml": None, "temperature": None, "count": 1})
        self.assertEqual(slots.clean(self.kaffee, strength="x", water_ml=1, temperature=7, count=0),
                         {"strength": None, "water_ml": 25, "temperature": None, "count": 1})
        # a sort without a machine product: only the number of cups
        self.assertEqual(slots.clean(self.tee, strength=8, water_ml=200, count=2),
                         {"strength": None, "water_ml": None, "temperature": None, "count": 2})

    def test_save_clear_and_what_the_machine_gets(self):
        slot = slots.save(self.member, 1, self.kaffee, strength=7, water_ml=200, temperature=2, count=2)
        self.assertEqual(slots.options(slot), {"strength": 7, "water_ml": 200})
        self.assertEqual(slots.describe(slot), "Kaffee, strength 7, 200 ml, high, 2x")
        self.member.favourite_slot = 1
        self.member.save()
        slots.save(self.member, 1, None)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.favourite_slot)
        self.assertFalse(MemberSlot.objects.exists())
        with self.assertRaises(ValueError):
            slots.save(self.member, 4, self.kaffee)

    def test_an_obsolete_sort_hides_its_slot(self):
        slots.save(self.member, 2, self.kaffee)
        self.kaffee.is_obsolete = True
        self.kaffee.save()
        self.assertEqual(slots.slots_for(self.member), [])

    @mock.patch.object(machine, "send", return_value=True)
    def test_cups_in_a_row_are_booked_one_by_one(self, send):
        first = machine.request_brew(self.product, self.member, self.kaffee, options={"strength": 7}, count=3)
        command = send.call_args.args[1]
        cups = list(MachineDispense.objects.order_by("cup"))
        self.assertEqual(command["request_ids"], [d.request_id for d in cups])
        self.assertEqual(command["options"], {"strength": 7})
        self.assertEqual([(d.cup, d.of, d.chain) for d in cups], [(1, 3, first.request_id)] * 1 + [
            (2, 3, first.request_id), (3, 3, first.request_id)])
        for cup in cups[:2]:
            machine.handle_event({"type": "brew_done", "request_id": cup.request_id, "product": 3})
        machine.handle_event({"type": "brew_failed", "request_id": cups[2].request_id, "product": 3,
                              "reason": "previous cup failed: blocked"})
        self.assertEqual(Order.objects.count(), 2)  # the third was never made
        self.assertIn("strength 7, cup 2/3", Order.objects.order_by("id").last().info)
        progress = machine.chain_progress(first)
        self.assertEqual((progress["done"], progress["over"], progress["failed"].cup), (2, True, 3))

    @mock.patch.object(machine, "send", return_value=True)
    def test_a_later_cup_waits_for_the_one_before(self, send):
        from django.utils import timezone

        first = machine.request_brew(self.product, self.member, self.kaffee, count=2)
        later = timezone.now() + timezone.timedelta(seconds=machine.REQUESTED_TIMEOUT + 10)
        MachineDispense.objects.filter(pk=first.pk).update(state=MachineDispense.STARTED)
        machine.housekeeping(later)
        self.assertEqual(MachineDispense.objects.get(cup=2).state, MachineDispense.REQUESTED)  # still waiting
        MachineDispense.objects.filter(pk=first.pk).update(state=MachineDispense.DONE, finished_at=later)
        machine.housekeeping(later + timezone.timedelta(seconds=machine.CHAIN_TIMEOUT + 1))
        self.assertEqual(MachineDispense.objects.get(cup=2).state, MachineDispense.FAILED)
