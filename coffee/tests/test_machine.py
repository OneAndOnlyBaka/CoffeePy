"""The app's side of the coffee machine bridge: what the listener makes of its topics, the config it
sends, the dashboard's search."""
import json
import time
from unittest import mock

from django.db import OperationalError
from django.test import TestCase, TransactionTestCase

from coffee import machine
from coffee.management.commands import mqtt_listener
from coffee.models import MachineStatus
from dashboard.models import CoffeeMachine


def set_up_machine(address="F4:57:52:42:0C:04", **fields):
    settings_ = CoffeeMachine.current()
    settings_.driver, settings_.address = CoffeeMachine.DRIVER_JURA_BLE, address
    for key, value in fields.items():
        setattr(settings_, key, value)
    settings_.save()
    return settings_


class ParseTestCase(TestCase):
    def test_topics(self):
        self.assertEqual(machine.parse("status", b"online"), "online")
        self.assertIsNone(machine.parse("status", b"exploded"))
        self.assertEqual(machine.parse("state", b'{"connected": true}'), {"connected": True})
        self.assertIsNone(machine.parse("state", b"{nope"))
        self.assertIsNone(machine.parse("state", b"[1, 2]"))
        self.assertIsNone(machine.parse("cmd", b"{}"))  # the app's own
        self.assertEqual(machine.parse("event", b""), {})

    def test_row_fields(self):
        self.assertEqual(machine.row_fields("status", "disabled"), {"status": "disabled", "online": True})
        self.assertEqual(machine.row_fields("status", "offline"), {"status": "offline", "online": False})
        self.assertEqual(machine.row_fields("counters", {"total": 3}), {"counters": {"total": 3}})
        self.assertIsNone(machine.row_fields("event", {}))

    def test_config_follows_the_dashboard(self):
        self.assertEqual(machine.config_payload(), {"driver": "none", "address": "", "block_manual": True})
        set_up_machine(block_manual=False)
        self.assertEqual(machine.config_payload(),
                         {"driver": "jura_ble", "address": "F4:57:52:42:0C:04", "block_manual": False})

    def test_send(self):
        with mock.patch("paho.mqtt.publish.single") as single:
            self.assertTrue(machine.send("config", {"a": 1}, retain=True))
        args, kwargs = single.call_args
        self.assertEqual((args[0], json.loads(args[1]), kwargs["retain"]), ("coffeemachine/config", {"a": 1}, True))
        with mock.patch("paho.mqtt.publish.single", side_effect=OSError("no broker")):
            self.assertFalse(machine.send("cmd", {}))


class ScanTestCase(TestCase):
    def test_a_search_and_its_answer(self):
        with mock.patch.object(machine, "send", return_value=True) as send:
            request_id = machine.request_scan()
        self.assertEqual(send.call_args.args[1]["action"], "scan")
        self.assertFalse(machine.scan_result(request_id)["done"])
        machine.handle_scan({"id": "someone else's", "devices": [{"address": "X"}]})
        self.assertFalse(machine.scan_result(request_id)["done"])
        machine.handle_scan({"id": request_id, "devices": [{"address": "F4:57:52:42:0C:04", "model": "E6 (EB)"}]})
        result = machine.scan_result(request_id)
        self.assertEqual((result["done"], result["devices"][0]["model"]), (True, "E6 (EB)"))
        self.assertEqual(machine.scan_result("old")["error"], "superseded")

    def test_no_bridge_no_answer(self):
        with mock.patch.object(machine, "send", return_value=True):
            request_id = machine.request_scan()
        MachineStatus.objects.filter(pk=1).update(scan={**MachineStatus.current().scan,
                                                        "requested": time.time() - machine.SCAN_SECONDS - 1})
        self.assertEqual(machine.scan_result(request_id)["error"], "timeout")
        with mock.patch.object(machine, "send", return_value=False):
            request_id = machine.request_scan()
        self.assertEqual(machine.scan_result(request_id)["error"], "unreachable")

    def test_an_answer_without_a_row(self):
        machine.handle_scan({"id": "x"})  # nothing asked: nothing written
        self.assertFalse(MachineStatus.objects.exists())


class StatusTestCase(TestCase):
    def test_status_and_counters(self):
        set_up_machine()
        machine.store({"online": True, "status": "online",
                       "state": {"connected": True, "address": "f4:57:52:42:0c:04",
                                 "alerts": ["coffee_ready", "fill_water", "bit_99"], "blocking": ["fill_water"]},
                       "products": {"products": [{"code": 2, "name": "Espresso"}]},
                       "counters": {"total": 30, "products": {"2": 20, "13": 10, "3": 0}}})
        status = machine.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["alerts"], ["fill_water"])  # the machine's mood and unknown bits aren't shown
        self.assertEqual(machine.counter_rows(status), [{"code": "2", "name": "Espresso", "count": 20},
                                                       {"code": "13", "name": "#13", "count": 10}])
        self.assertEqual(machine.alert_label("fill_water"), "Fill the water tank")
        self.assertEqual(machine.alert_label("bit_99"), "bit_99")

    def test_another_machine_reporting_is_not_this_one(self):
        set_up_machine(address="AA:BB:CC:DD:EE:FF")
        machine.store({"online": True, "state": {"connected": True, "address": "F4:57:52:42:0C:04"}})
        self.assertFalse(machine.status()["connected"])

    def test_nothing_reported(self):
        status = machine.status()
        self.assertEqual((status["bridge"], status["connected"], status["products"]), ("", False, []))


# The listener's rows: written with retries, checked again after a restore (like the reader's).
class MachineRowTestCase(TransactionTestCase):
    def test_a_failed_write_is_retried(self):
        row = mqtt_listener.MachineRow()
        with mock.patch.object(machine, "store", side_effect=OperationalError("starting up")):
            row.set_fields(status="online", online=True)
        self.assertFalse(MachineStatus.objects.exists())
        row.tried_at -= mqtt_listener.STATUS_RETRY_SECONDS
        row.set_fields(state={"connected": True})
        current = MachineStatus.current()
        self.assertEqual((current.status, current.state), ("online", {"connected": True}))

    def test_a_replaced_row_is_written_again(self):
        row = mqtt_listener.MachineRow()
        row.set_fields(status="online", online=True)
        MachineStatus.objects.all().delete()
        row.checked_at -= mqtt_listener.STATUS_CHECK_SECONDS
        row.flush()
        self.assertTrue(MachineStatus.current().online)
        self.assertEqual(row.describe({"b": 1, "a": 2}), "a, b")


class MachineConfigTestCase(TestCase):
    def test_sent_on_connect_and_when_it_differs(self):
        client = mock.Mock()
        config = mqtt_listener.MachineConfig()
        config.push(client, force=True)
        self.assertEqual(client.publish.call_count, 1)
        self.assertEqual(client.publish.call_args.args[0], "coffeemachine/config")
        config.push(client)  # too soon to look again
        set_up_machine()
        config.checked_at -= mqtt_listener.STATUS_CHECK_SECONDS
        config.push(client)
        self.assertEqual(json.loads(client.publish.call_args.args[1])["driver"], "jura_ble")
        config.checked_at -= mqtt_listener.STATUS_CHECK_SECONDS
        config.push(client)  # unchanged
        self.assertEqual(client.publish.call_count, 2)
