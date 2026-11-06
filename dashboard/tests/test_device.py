"""The coffee machine page: choosing a machine (or none), the host's Bluetooth, searching and
connecting, what the machine reports - and the overview's row."""
import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from coffee import machine
from coffee.models import AuditLogEntry
from dashboard.models import CoffeeMachine


class DeviceTestCase(TestCase):
    def setUp(self):
        self.storage = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.storage)
        override = override_settings(STORAGE_DIR=self.storage)
        override.enable()
        self.addCleanup(override.disable)
        patcher = mock.patch.object(machine, "send", return_value=True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        User.objects.create_superuser(username="root", password="pw")
        self.client = Client()
        self.client.login(username="root", password="pw")
        self.url = reverse("dashboard:device")

    def helper_status(self, **bluetooth):
        (self.storage / "status.json").write_text(json.dumps({"updated": time.time(), "bluetooth": bluetooth}))

    def requests(self):
        return [json.loads(p.read_text()) for p in sorted((self.storage / "requests").glob("*.json"))]

    def save(self, **fields):
        return self.client.post(self.url, {"form": "settings", "driver": "jura_ble", "adapter": "usb",
                                           "block_manual": "on", **fields})

    def test_managers_dont_get_here(self):
        User.objects.create_user(username="m", password="pw")
        self.client.login(username="m", password="pw")
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_none_by_default(self):
        response = self.client.get(self.url)
        self.assertContains(response, "None - the tally only")
        self.assertNotContains(response, "Search for machines")
        self.assertNotContains(self.client.get(reverse("dashboard:index")), 'list-row-title">Coffee machine')

    def test_choosing_a_jura_switches_bluetooth_on_and_tells_the_bridge(self):
        self.assertRedirects(self.save(), self.url)
        self.assertEqual(CoffeeMachine.current().driver, "jura_ble")
        (request,) = self.requests()
        self.assertEqual((request["action"], request["enabled"], request["onboard"]), ("bluetooth", True, False))
        self.assertEqual(self.send.call_args.args[:2], ("config", machine.config_payload()))
        self.assertEqual(AuditLogEntry.objects.get(action="coffee_machine.update").details,
                         "jura_ble, usb, keys locked")
        # Saved again unchanged: no second request to the host.
        self.save()
        self.assertEqual(len(self.requests()), 1)

    def test_the_pis_own_bluetooth_needs_a_restart(self):
        self.save(adapter="onboard")
        self.assertTrue(self.requests()[0]["onboard"])
        self.helper_status(active=True, adapters=[], restart_needed=True)
        self.assertContains(self.client.get(self.url), "Restart the device")
        self.client.post(self.url, {"form": "reboot"})
        self.assertIn("reboot", [r["action"] for r in self.requests()])
        self.assertTrue(AuditLogEntry.objects.filter(action="system.reboot").exists())

    def test_bluetooth_states(self):
        self.save()
        self.assertContains(self.client.get(self.url), "storage service is not running")
        self.helper_status(active=False, adapters=[])
        self.assertContains(self.client.get(self.url), "being switched on")
        self.helper_status(active=True, adapters=[])
        self.assertContains(self.client.get(self.url), "is the USB stick plugged in")
        self.helper_status(active=True, adapters=["hci0"], error="")
        self.assertContains(self.client.get(self.url), "hci0")

    def test_search_and_connect(self):
        self.save()
        response = self.client.post(reverse("dashboard:device_scan"))
        request_id = response.json()["id"]
        self.assertEqual(self.send.call_args.args[1], {"id": request_id, "action": "scan"})
        machine.handle_scan({"id": request_id, "devices": [{"address": "F4:57:52:42:0C:04", "model": "E6 (EB)"}]})
        answer = self.client.get(reverse("dashboard:device_scan_status"), {"id": request_id}).json()
        self.assertEqual(answer["devices"][0]["model"], "E6 (EB)")
        self.client.post(self.url, {"form": "connect", "address": "f4:57:52:42:0c:04", "name": "JURA E6 (EB)"})
        settings_ = CoffeeMachine.current()
        self.assertEqual((settings_.address, settings_.name), ("F4:57:52:42:0C:04", "JURA E6 (EB)"))
        self.assertEqual(AuditLogEntry.objects.get(action="coffee_machine.connect").target, "F4:57:52:42:0C:04")
        self.assertEqual(self.send.call_args.args[1]["address"], "F4:57:52:42:0C:04")

    def test_connecting_takes_only_an_address(self):
        self.save()
        self.client.post(self.url, {"form": "connect", "address": "'; DROP"})
        self.assertEqual(CoffeeMachine.current().address, "")
        self.client.post(self.url, {"form": "settings", "driver": "none", "adapter": "usb"})
        self.client.post(self.url, {"form": "connect", "address": "F4:57:52:42:0C:04"})  # no machine chosen
        self.assertEqual(CoffeeMachine.current().address, "")
        self.assertEqual(self.client.post(reverse("dashboard:device_scan")).status_code, 400)

    def test_what_the_machine_reports(self):
        self.save()
        self.client.post(self.url, {"form": "connect", "address": "F4:57:52:42:0C:04", "name": "JURA E6 (EB)"})
        machine.store({"online": True, "status": "online",
                       "state": {"connected": True, "address": "F4:57:52:42:0C:04", "firmware": "EF532M V02.03",
                                 "alerts": ["fill_water", "lockedkeys"], "blocking": ["fill_water"]},
                       "products": {"products": [{"code": 2, "name": "Espresso"}]},
                       "counters": {"total": 4214, "products": {"2": 1192},
                                    "maintenance": {"counters": {"cleaning": 37}, "percent": {"cleaning": 40}}}})
        response = self.client.get(self.url)
        for text in ("JURA E6 (EB)", "EF532M V02.03", "Fill the water tank", "Keys locked", "blocked",
                     "so far: 4214", "Espresso", "1192", "40% due"):
            self.assertContains(response, text)
        overview = self.client.get(reverse("dashboard:index"))
        self.assertContains(overview, "Fill the water tank")
        self.assertContains(overview, "blocked")

    def test_disconnect_and_switch_off(self):
        self.save()
        self.client.post(self.url, {"form": "connect", "address": "F4:57:52:42:0C:04"})
        self.client.post(self.url, {"form": "forget"})
        self.assertEqual(CoffeeMachine.current().address, "")
        self.assertTrue(AuditLogEntry.objects.filter(action="coffee_machine.forget").exists())
        self.client.post(self.url, {"form": "connect", "address": "F4:57:52:42:0C:04"})
        self.client.post(self.url, {"form": "settings", "driver": "none", "adapter": "usb"})
        settings_ = CoffeeMachine.current()
        self.assertEqual((settings_.driver, settings_.address), ("none", ""))
        self.assertIn(False, [r["enabled"] for r in self.requests()])  # Bluetooth off again

    def test_a_broker_that_isnt_there(self):
        self.send.return_value = False
        response = self.client.post(self.url, {"form": "settings", "driver": "jura_ble", "adapter": "usb"}, follow=True)
        self.assertContains(response, "takes it over within a minute")


class DeviceProductsTestCase(TestCase):
    """The page's products, supplies and numbers - and the inventory's consumption per product."""

    def setUp(self):
        from decimal import Decimal

        from coffee.models import CoffeeSort
        patcher = mock.patch.object(machine, "send", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address = "jura_ble", "F4:57:52:42:0C:04"
        settings_.save()
        self.espresso = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.40"), machine_strokes=1)
        self.latte = CoffeeSort.objects.create(name="Latte", price=Decimal("0.80"), machine_strokes=1)
        machine.sync_products({"products": [{"code": 2, "name": "Espresso", "strokes": 1, "brewable": True},
                                            {"code": 19, "name": "2 Coffee", "strokes": 2, "brewable": True},
                                            {"code": 13, "name": "Hot Water", "brewable": False}]})
        User.objects.create_superuser(username="root", password="pw")
        self.client = Client()
        self.client.login(username="root", password="pw")
        self.url = reverse("dashboard:device")

    def test_linking_by_hand_unlinks_the_obsolete(self):
        from coffee.models import CoffeeSort, MachineProduct
        self.assertTrue(CoffeeSort.objects.get(name="Latte").is_obsolete)
        self.assertContains(self.client.get(reverse("dashboard:coffeesorts")), "obsolete")
        self.client.post(self.url, {"form": "products", "p2-present": "1", "p2-sort": str(self.espresso.pk), "p2-strokes": "1",
                                    "p19-present": "1", "p19-sort": str(self.latte.pk), "p19-strokes": "2",
                                    "p13-present": "1", "p13-free": "on", "p13-strokes": "1", "p13-water": "220"})
        self.assertEqual(MachineProduct.objects.get(code=19).coffee_sort, self.latte)
        self.assertEqual(MachineProduct.objects.get(code=13).water_ml, 220)
        self.assertFalse(CoffeeSort.objects.get(name="Latte").is_obsolete)
        self.assertTrue(AuditLogEntry.objects.filter(action="machine_product.update").exists())
        # One sort, one product: the second taker is left without.
        self.client.post(self.url, {"form": "products", "p2-present": "1", "p2-sort": str(self.latte.pk), "p2-strokes": "1",
                                    "p19-present": "1", "p19-sort": str(self.latte.pk), "p19-strokes": "2"})
        self.assertEqual(MachineProduct.objects.filter(coffee_sort=self.latte).count(), 1)

    def test_adopting_as_a_new_sort(self):
        from coffee.models import CoffeeSort
        response = self.client.post(self.url, {"form": "adopt", "code": "19"})
        sort = CoffeeSort.objects.get(name="2 Coffee")
        self.assertRedirects(response, reverse("dashboard:coffeesort_edit", args=[sort.pk]))
        self.assertEqual(sort.machine_strokes, 2)
        self.assertEqual(self.client.post(self.url, {"form": "adopt", "code": "19"}).status_code, 404)  # linked now

    def test_supplies_and_numbers(self):
        from coffee.models import MachineDispense, MachineSupply
        self.client.post(self.url, {"form": "supplies", "water-capacity": "1500", "grounds-capacity": "16",
                                    "beans-capacity": "280", "beans-per_portion": "9"})
        self.assertEqual(machine.supplies()[MachineSupply.WATER].capacity, 1500)
        self.assertEqual(machine.supplies()[MachineSupply.BEANS].per_portion, 9)
        water = machine.supplies()[MachineSupply.WATER]
        water.used = 700
        water.save()
        self.client.post(self.url, {"form": "refill", "kind": "water"})
        self.assertEqual(machine.supplies()[MachineSupply.WATER].used, 0)
        MachineDispense.objects.create(code=2, product_name="Espresso", source="manual", state="done")
        MachineDispense.objects.create(code=2, product_name="Espresso", source="app", state="failed", reason="busy")
        response = self.client.get(self.url)
        for text in ("Products made", "At the machine", "busy", "Supplies", "Tank (ml)", "As a new sort"):
            self.assertContains(response, text)

    def test_switching_off_offers_everything_again(self):
        from coffee.models import CoffeeSort
        self.client.post(self.url, {"form": "settings", "driver": "none", "adapter": "usb"})
        self.assertFalse(CoffeeSort.objects.filter(is_obsolete=True).exists())

    def test_a_sort_without_the_machine(self):
        from coffee.models import CoffeeSort
        self.client.post(reverse("dashboard:coffeesort_edit", args=[self.latte.pk]),
                         {"name": "Latte", "price": "0.80", "machine_strokes": "1", "without_machine": "on"})
        self.assertFalse(CoffeeSort.objects.get(name="Latte").is_obsolete)

    def test_consumption_per_product(self):
        from coffee.models import InventoryConsumption, InventoryItem
        from dashboard.models import InventoryPolicy
        InventoryPolicy.objects.update_or_create(pk=1, defaults={"mode": "dashboard"})
        beans = InventoryItem.objects.create(name="Beans", unit="kg")
        url = reverse("dashboard:inventory_item_edit", args=[beans.pk])
        self.assertContains(self.client.get(url), "Used by the coffee machine")
        self.client.post(url, {"name": "Beans", "unit": "kg", "is_active": "on", "order": "0", "use-2": "0,008"})
        self.assertEqual(str(InventoryConsumption.objects.get().amount), "0.008")
        self.client.post(url, {"name": "Beans", "unit": "kg", "is_active": "on", "order": "0", "use-2": ""})
        self.assertFalse(InventoryConsumption.objects.exists())
