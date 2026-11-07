"""The coffee machine's data in the app: its products against the coffee sorts, its brews into
orders, its supplies and who left them for the next one, its counters in the inventory."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from coffee import machine, services
from coffee.models import (
    AuditLogEntry, CoffeeSort, InventoryConsumption, InventoryItem, MachineCounter, MachineDispense, MachineIncident,
    MachineProduct, MachineSupply, Member, Order, StockCount,
)
from dashboard.models import CoffeeMachine

E6 = {"products": [
    {"code": 2, "name": "Espresso", "strokes": 1, "water_ml": 45, "brewable": True},
    {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "brewable": True},
    {"code": 13, "name": "Hot Water", "strokes": 1, "brewable": False},
    {"code": 19, "name": "2 Coffee", "strokes": 2, "brewable": True},
]}


def configure(**fields):
    settings_ = CoffeeMachine.current()
    settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, "F4:57:52:42:0C:04"
    for key, value in fields.items():
        setattr(settings_, key, value)
    settings_.save()


def sort(name, price="0.50", strokes=1, **fields):
    return CoffeeSort.objects.create(name=name, price=Decimal(price), machine_strokes=strokes, **fields)


class SyncTestCase(TestCase):
    def setUp(self):
        configure()
        self.espresso, self.kaffee = sort("Espresso", "0.40"), sort("Kaffee")
        self.latte, self.tee = sort("Latte Macchiato", "0.80"), sort("Tee", "0.20", without_machine=True)

    def test_sorts_are_matched_and_the_rest_obsolete(self):
        changes = machine.sync_products(E6)
        self.assertEqual(MachineProduct.objects.get(code=2).coffee_sort, self.espresso)
        self.assertEqual(MachineProduct.objects.get(code=3).coffee_sort, self.kaffee)  # Coffee = Kaffee
        self.assertIsNone(MachineProduct.objects.get(code=13).coffee_sort)  # free, not sold
        self.assertTrue(MachineProduct.objects.get(code=13).free)
        self.assertIsNone(MachineProduct.objects.get(code=19).coffee_sort)  # a suggestion to adopt
        self.assertEqual(changes["obsolete"], ["Latte Macchiato"])
        self.assertEqual(list(CoffeeSort.offered().order_by("name").values_list("name", flat=True)),
                         ["Espresso", "Kaffee", "Tee"])  # tea needs no machine

    def test_a_product_the_machine_no_longer_lists(self):
        machine.sync_products(E6)
        machine.sync_products({"products": [p for p in E6["products"] if p["code"] != 3]})
        self.assertFalse(MachineProduct.objects.get(code=3).present)
        self.assertTrue(CoffeeSort.objects.get(name="Kaffee").is_obsolete)
        machine.sync_products(E6)  # back: its link was kept
        self.assertFalse(CoffeeSort.objects.get(name="Kaffee").is_obsolete)

    def test_nothing_without_a_machine_or_a_list(self):
        self.assertEqual(machine.sync_products({"products": []}), {})
        CoffeeMachine.objects.filter(pk=1).update(driver="none")
        self.assertEqual(machine.sync_products(E6), {})
        self.assertFalse(CoffeeSort.objects.filter(is_obsolete=True).exists())

    def test_switched_off_everything_is_offered_again(self):
        machine.sync_products(E6)
        self.assertEqual(machine.unsync_products(), ["Latte Macchiato"])
        self.assertFalse(CoffeeSort.objects.filter(is_obsolete=True).exists())

    def test_adopting_a_product(self):
        machine.sync_products(E6)
        new = machine.adopt_product(MachineProduct.objects.get(code=19))
        self.assertEqual((new.name, new.machine_strokes), ("2 Coffee", 2))
        # Twice what the sorts sold cost per stroke on average (0.40, 0.50 and the tea's 0.20).
        self.assertEqual(new.price, Decimal("0.75"))
        sort("Hot Water")
        hot = machine.adopt_product(MachineProduct.objects.get(code=13))
        self.assertEqual(hot.name, "Hot Water (2)")
        CoffeeSort.objects.all().update(price=0)
        self.assertEqual(machine.suggested_price(1), Decimal("0.00"))

    def test_product_labels_and_brewing(self):
        machine.sync_products(E6)
        self.assertEqual(machine.product_label("Hot Water"), "Hot water")
        hot = MachineProduct.objects.get(code=13)
        self.assertFalse(machine.can_brew(hot))  # no recipe, no water given
        hot.water_ml = 200
        self.assertTrue(machine.can_brew(hot))
        self.assertIsNone(machine.product_for(None))
        self.assertEqual(machine.product_for(self.espresso).code, 2)


class BrewTestCase(TestCase):
    def setUp(self):
        configure()
        self.espresso = sort("Espresso", "0.40")
        machine.sync_products(E6)
        self.member = Member.objects.create(uid=7, nickname="Anna B.")
        self.product = MachineProduct.objects.get(code=2)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def brew(self):
        return machine.request_brew(self.product, self.member, self.espresso)

    def test_a_kiosk_brew_is_booked_once_it_is_done(self):
        dispense = self.brew()
        self.assertEqual(self.send.call_args.args[1]["product"], 2)
        machine.handle_event({"type": "brew_started", "request_id": dispense.request_id, "product": 2})
        self.assertFalse(Order.objects.exists())
        machine.handle_event({"type": "brew_done", "request_id": dispense.request_id, "product": 2,
                              "water_ml": 50, "strength": 8})
        machine.handle_event({"type": "brew_done", "request_id": dispense.request_id, "product": 2})  # again
        order = Order.objects.get()
        self.assertEqual((order.member, order.value, order.coffee_sort), (self.member, Decimal("0.40"), self.espresso))
        dispense.refresh_from_db()
        self.assertEqual((dispense.state, dispense.order, dispense.water_ml), (MachineDispense.DONE, order, 50))
        entry = AuditLogEntry.objects.get(action="order.create")
        self.assertEqual(entry.source, "kiosk")
        self.assertIn("made by the machine", entry.details)
        self.assertEqual(machine.supplies()[MachineSupply.WATER].used, 50)
        self.assertEqual(machine.supplies()[MachineSupply.BEANS].used, 8)

    def test_a_failed_brew_books_nothing(self):
        dispense = self.brew()
        machine.handle_event({"type": "brew_failed", "request_id": dispense.request_id, "reason": "blocked"})
        dispense.refresh_from_db()
        self.assertEqual((dispense.state, dispense.reason), (MachineDispense.FAILED, "blocked"))
        self.assertFalse(Order.objects.exists())

    def test_an_unreachable_bridge(self):
        self.send.return_value = False
        self.assertEqual(self.brew().reason, "unreachable")

    def test_a_product_without_a_recipe_takes_its_water(self):
        hot = MachineProduct.objects.get(code=13)
        hot.water_ml = 200
        hot.save()
        machine.request_brew(hot, None, None, MachineDispense.SOURCE_FREE)
        self.assertEqual(self.send.call_args.args[1]["options"], {"water_ml": 200})
        machine.sync_products(E6)  # every reconnect: the machine says nothing about it, the page's stays
        hot.refresh_from_db()
        self.assertEqual(hot.water_ml, 200)

    def test_a_brew_at_the_machine(self):
        machine.handle_event({"type": "brew_started", "request_id": "", "source": "manual", "product": 3})
        dispense = machine.handle_event({"type": "brew_done", "request_id": "", "product": 3, "water_ml": 100})
        self.assertEqual((dispense.source, dispense.state, dispense.order), ("manual", "done", None))
        self.assertIsNone(machine.handle_event({"type": "brew_done", "request_id": "", "product": 3}))

    def test_timeouts(self):
        started, requested = self.brew(), self.brew()
        machine.handle_event({"type": "brew_started", "request_id": started.request_id, "product": 2})
        later = timezone.now() + timedelta(seconds=machine.STARTED_TIMEOUT + 1)
        machine.housekeeping(later)
        started.refresh_from_db()
        requested.refresh_from_db()
        self.assertEqual((started.state, started.reason), ("done", "no confirmation"))
        self.assertEqual(Order.objects.get().member, self.member)  # most likely made
        self.assertEqual((requested.state, requested.reason), ("failed", "no answer"))

    def test_counters_are_kept_when_they_move(self):
        self.assertIsNone(machine.record_counters({"total": 0, "products": {"2": 0}}))
        machine.record_counters({"total": 10, "products": {"2": 10, "3": 0}})
        machine.record_counters({"total": 10, "products": {"2": 10}})
        machine.record_counters({"total": 11, "products": {"2": 11}})
        self.assertEqual(MachineCounter.objects.count(), 2)


class SupplyTestCase(TestCase):
    def setUp(self):
        configure()
        self.espresso = sort("Espresso")
        machine.sync_products(E6)
        self.anna, self.ben = Member.objects.create(uid=1, nickname="Anna B."), Member.objects.create(uid=2, nickname="Ben C.")

    def done_brew(self, member, finished=None):
        return MachineDispense.objects.create(
            code=2, source=MachineDispense.SOURCE_KIOSK, member=member, state=MachineDispense.DONE,
            finished_at=finished or timezone.now())

    def test_refilled_when_the_message_goes(self):
        water = machine.supplies()[MachineSupply.WATER]
        water.used = 1500
        water.save()
        machine.track_alerts(["fill_water"], [])
        self.assertEqual(machine.supplies()[MachineSupply.WATER].used, 0)
        self.assertTrue(AuditLogEntry.objects.filter(action="machine.refill", target="water").exists())

    def test_the_tasks_before_and_after(self):
        found = machine.supplies()
        espresso = MachineProduct.objects.get(code=2)
        self.assertEqual(machine.tasks_before(espresso), [])
        found[MachineSupply.WATER].used = 1880
        found[MachineSupply.WATER].save()
        found[MachineSupply.BEANS].used = 250
        found[MachineSupply.BEANS].save()
        self.assertEqual(machine.tasks_before(espresso), ["Fill the water tank", "Fill in coffee beans"])
        found[MachineSupply.GROUNDS].used = 12
        found[MachineSupply.GROUNDS].save()
        self.assertEqual(machine.tasks_after(espresso), ["Empty the grounds container", "Fill the water tank"])
        self.assertEqual(machine.supply_levels()["supplies"][0]["kind"], "water")

    def test_who_ran_off_and_who_stepped_in(self):
        self.done_brew(self.anna)
        machine.track_alerts([], ["empty_grounds"])
        incident = MachineIncident.objects.get()
        self.assertEqual((incident.member, incident.outcome), (self.anna, "open"))
        self.assertEqual(machine.open_tasks(self.anna), [{"alert": "empty_grounds", "label": "Empty the grounds container"}])
        machine.incidents_housekeeping(timezone.now() + timedelta(seconds=machine.INCIDENT_GRACE + 1))
        incident.refresh_from_db()
        self.assertEqual(incident.outcome, "left")
        machine.note_visitor(self.ben)
        machine.track_alerts(["empty_grounds"], [])
        incident.refresh_from_db()
        self.assertEqual((incident.hero, incident.cleared_at is not None), (self.ben, True))
        self.assertEqual(machine.runaway_board(), [
            {"uid": 1, "name": "Anna B.", "left": 1, "heroic": 0},
            {"uid": 2, "name": "Ben C.", "left": 0, "heroic": 1}])
        self.assertTrue(AuditLogEntry.objects.filter(action="machine_incident.hero").exists())

    def test_done_in_time_or_late_by_themselves(self):
        self.done_brew(self.anna)
        machine.track_alerts([], ["fill_water"])
        machine.track_alerts(["fill_water"], [])
        self.assertEqual(MachineIncident.objects.get().outcome, "fixed")
        machine.track_alerts([], ["fill_water"])
        incident = MachineIncident.objects.filter(cleared_at__isnull=True).get()
        incident.outcome = "left"
        incident.save()
        machine.note_visitor(self.anna)  # came back
        machine.track_alerts(["fill_water"], [])
        incident.refresh_from_db()
        self.assertIsNone(incident.hero)

    def test_no_incident_without_a_brew_just_now(self):
        self.done_brew(self.anna, finished=timezone.now() - timedelta(seconds=machine.INCIDENT_WINDOW + 5))
        machine.track_alerts([], ["no_beans"])
        self.assertFalse(MachineIncident.objects.exists())


class InventoryTestCase(TestCase):
    def setUp(self):
        configure()
        machine.sync_products(E6)
        self.beans = InventoryItem.objects.create(name="Beans", unit="kg")
        InventoryConsumption.objects.create(item=self.beans, product=MachineProduct.objects.get(code=2), amount=Decimal("0.008"))
        InventoryConsumption.objects.create(item=self.beans, product=MachineProduct.objects.get(code=19), amount=Decimal("0.016"))
        self.start = timezone.now() - timedelta(days=10)

    def reading(self, when, **counts):
        MachineCounter.objects.create(taken_at=when, total=sum(counts.values()),
                                      products={k.lstrip("p"): v for k, v in counts.items()})

    def test_expected_stock_from_the_counters(self):
        self.reading(self.start - timedelta(minutes=5), p2=100, p19=10)
        StockCount.objects.create(item=self.beans, quantity=Decimal("2"), created_at=self.start)
        self.reading(timezone.now(), p2=150, p19=20)
        level = services.stock_levels()[0]
        # 50 espressi x 8 g + 10 double coffees x 16 g = 0.56 kg
        self.assertEqual((level["machine_used"], level["expected"]), (Decimal("0.560"), Decimal("1.440")))
        self.assertEqual(services.machine_cups(self.start), 50 + 10 * 2)

    def test_without_counters_covering_it(self):
        StockCount.objects.create(item=self.beans, quantity=Decimal("2"), created_at=self.start)
        self.reading(timezone.now(), p2=150)
        self.assertIsNone(services.stock_levels()[0]["expected"])
        self.assertIsNone(services.machine_cups(self.start))
        self.assertIsNone(services.machine_used(InventoryItem.objects.create(name="Milk"), self.start))

    def test_cost_per_cup_counts_the_cups_past_the_tally(self):
        self.reading(timezone.now() - timedelta(days=400), p2=0)
        self.reading(timezone.now(), p2=300)
        cost = services.cost_per_cup(12)
        self.assertEqual((cost["cups"], cost["booked_cups"], cost["cups_from_machine"]), (300, 0, True))
