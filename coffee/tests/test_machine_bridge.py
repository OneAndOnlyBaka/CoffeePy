"""The coffee machine bridge's pure parts (coffee-machine/codec.py, policy.py, ha.py) - fed with the
frames a JURA E6 sent on 2026-11-06, decoded, so a change that misreads them shows here and not
only at the machine. The bridge itself needs bleak and a machine; these need neither."""
import json
import sys

from django.conf import settings
from django.test import SimpleTestCase

sys.path.insert(0, str(settings.BASE_DIR / "coffee-machine"))
import codec  # noqa: E402
import ha  # noqa: E402
import policy  # noqa: E402

sys.path.pop(0)

H = bytes.fromhex
# A manual espresso (key 0x2a, strength 8, 50 ml) and one asked for over Bluetooth (key 0x0a,
# strength 6, 45 ml, high temperature).
MANUAL = [
    "2a00000000000000000000000000000100000000",
    "2a39020808000affffffff11ffff110000ff0000",
    "2a21028f030a0000000000000000000000000000",
    "2a3c020808000affffffff11ffff110000ff0000",
    "2a3c020808050affffffff11ffff110032ff0000",
    "2a3e020000000000000000000000000000000000",
]
APP_STARTING = "0a390206060009ffffffff22ffff110000ff0000"
COUNTERS = ("0010760000000004a8000a5100013700ffff00000000ffff00001400ffff00ffff00ffff00ffff00001d00ffff00ffff00ff"
            "ff00000000000000000c00ffff00ffff00ffff00ffff00ffff00ffff00ffff00ffff00ffff00ffff00ffff00ffff00000000"
            "000000000000000000ffff00ffff00ffff00ffff00000000000000000000000000ffff00ffff00ffff00ffff000000000000"
            "00000000000000ffff00ffff00ffff00ffff0000000000000000000000000000000000000000000000000000000000000000")


class CodecTestCase(SimpleTestCase):
    def test_progress_of_a_manual_espresso(self):
        phases = [codec.progress(H(f))["phase"] for f in MANUAL]
        self.assertEqual(phases, ["idle", "starting", "heating", "dispensing", "dispensing", "done"])
        self.assertEqual(codec.progress(H(MANUAL[0]))["product"], 0)
        starting = codec.progress(H(MANUAL[1]))
        self.assertEqual((starting["product"], starting["strength"], starting["water_ml"], starting["temperature"]),
                         (2, 8, 50, 1))
        self.assertEqual(codec.progress(H(MANUAL[4]))["percent"], 50)
        self.assertEqual(codec.progress(H(MANUAL[2]))["percent"], 30)

    def test_the_recipe_of_a_brew_asked_for(self):
        frame = codec.progress(H(APP_STARTING))
        self.assertEqual((frame["strength"], frame["water_ml"], frame["temperature"]), (6, 45, 2))

    def test_short_and_unknown_frames(self):
        self.assertEqual(codec.progress(b"")["phase"], "idle")
        self.assertEqual(codec.progress(H("2a24"))["phase"], "busy")  # py-jura's "ready", never seen on an E6
        self.assertNotIn("percent", codec.progress(H("2a2102000000"[:12] + "00"))
                         )  # heating without a step count
        self.assertTrue(codec.is_active(codec.STATE_DISPENSING))
        self.assertFalse(codec.is_active(codec.STATE_DONE))

    def test_a_frame_for_another_key_is_not_believed(self):
        self.assertTrue(codec.sound(H(MANUAL[0]), 0x2A))
        self.assertTrue(codec.sound(H(APP_STARTING), 0x0A))
        self.assertFalse(codec.sound(H(APP_STARTING), 0x2A))
        self.assertFalse(codec.sound(b"\x2a", 0x2A))

    def test_a_garbled_frame_needs_a_second_read(self):
        """The two status frames the E6 sent through a Pi 3's own Bluetooth on 2026-11-23, 1.5 s apart."""
        good, garbled = H("2a000400000d0000000000000000000000000006"), H("2a000400000ddcde8026f318a4abfaa8d258a763")
        self.assertTrue(codec.sound(garbled, 0x2A))  # the key byte survived - not enough on its own
        self.assertIsNone(codec.confirmed(good, garbled, good))
        self.assertEqual(codec.confirmed(good, good, None), good)  # unchanged: no second read needed
        changed = H("2a000400000c0000000000000000000000000006")
        self.assertEqual(codec.confirmed(good, changed, changed), changed)
        self.assertEqual(codec.confirmed(None, good, good), good)  # the first frame of a session

    def test_counters(self):
        counted = codec.counters(H(COUNTERS))
        self.assertEqual(counted["total"], 4214)
        self.assertEqual({k: v for k, v in counted["products"].items() if v},
                         {0x02: 1192, 0x03: 2641, 0x04: 311, 0x08: 20, 0x0D: 29, 0x13: 12})
        self.assertNotIn(0x05, counted["products"])  # 0xFFFF: no such product on this machine
        self.assertEqual(codec.counters(b""), {"total": 0, "products": {}})

    def test_maintenance(self):
        result = codec.maintenance(H("002500100003006316e00044"), bytes([40, 7, 101]),
                                   ("cleaning", "filter_change", "decalc", "cappu_rinse", "coffee_rinse",
                                    "cappu_clean", "unknown"), ("cleaning", "filter_change", "decalc", "more"))
        self.assertEqual(result["counters"], {"cleaning": 37, "filter_change": 16, "decalc": 3, "cappu_rinse": 99,
                                              "coffee_rinse": 5856, "cappu_clean": 68})
        self.assertEqual(result["percent"], {"cleaning": 40, "filter_change": 7})  # 101: not a percentage

    def test_alert_bits(self):
        # The E6 idle and ready: coffee_ready (13), energy_safe (36), active_rf_filter (37).
        self.assertEqual(codec.alert_bits(H("2a000400000c0000")), [13, 36, 37])

    def test_advertisement(self):
        self.assertEqual(codec.advertisement(H("2a050803113c1ad020")), (0x2A, 15377))
        self.assertIsNone(codec.advertisement(b""))  # some advertisements come without it
        self.assertIsNone(codec.advertisement(b"\x2a\x05"))


class BrewTrackerTestCase(SimpleTestCase):
    def feed(self, tracker, frames, start=0.0):
        events = []
        for i, frame in enumerate(frames):
            events += tracker.update(codec.progress(H(frame)), start + i)
        return events

    def test_a_brew_asked_for(self):
        tracker = policy.BrewTracker()
        tracker.expect("r1", 2, 0.0)
        self.assertTrue(tracker.busy())
        frames = [MANUAL[0], MANUAL[2], APP_STARTING, MANUAL[4], MANUAL[5], MANUAL[5]]
        events = self.feed(tracker, frames)
        self.assertEqual([e["type"] for e in events], ["brew_started", "brew_done"])
        self.assertEqual((events[0]["request_id"], events[0]["source"]), ("r1", "app"))
        # The recipe as the machine reported it - from the brew-start frame, the later ones carry
        # the dispensing frame's (here the manual one's 50 ml, a fixture shortcut).
        self.assertEqual(events[1]["product"], 2)
        self.assertIn("water_ml", events[1])
        self.assertFalse(tracker.busy())

    def test_a_brew_at_the_machine(self):
        events = self.feed(policy.BrewTracker(), MANUAL)
        self.assertEqual([(e["type"], e["source"], e["request_id"]) for e in events],
                         [("brew_started", "manual", None), ("brew_done", "manual", None)])
        self.assertEqual((events[1]["water_ml"], events[1]["strength"]), (50, 8))

    def test_another_product_than_asked_for_is_the_machines(self):
        tracker = policy.BrewTracker()
        tracker.expect("r1", 3, 0.0)  # coffee asked for, an espresso chosen at the machine
        events = self.feed(tracker, MANUAL[:2])
        self.assertEqual(events[0]["source"], "manual")
        self.assertIsNotNone(tracker.pending)

    def test_a_brew_that_never_starts(self):
        tracker = policy.BrewTracker()
        tracker.expect("r1", 2, 0.0, source="free")
        events = tracker.update(codec.progress(H(MANUAL[0])), policy.START_TIMEOUT + 1)
        self.assertEqual(events, [{"type": "brew_failed", "request_id": "r1", "source": "free", "product": 2,
                                   "reason": "not started"}])

    def test_a_brew_that_stalls(self):
        tracker = policy.BrewTracker()
        self.feed(tracker, MANUAL[:2])
        events = tracker.update(codec.progress(H(MANUAL[1])), policy.STALL_TIMEOUT + 5)
        self.assertEqual(events[0]["reason"], "stalled")
        self.assertFalse(tracker.busy())

    def test_a_missed_done_frame(self):
        events = self.feed(policy.BrewTracker(), [MANUAL[1], MANUAL[3], MANUAL[0]])
        self.assertEqual([e["type"] for e in events], ["brew_started", "brew_done"])

    def test_the_connection_lost(self):
        tracker = policy.BrewTracker()
        tracker.expect("r1", 2, 0.0)
        self.assertEqual(tracker.lost()[0]["reason"], "disconnected")
        self.feed(tracker, MANUAL[:2])
        self.assertEqual(tracker.lost(), [])  # a brew that ran: the app decides
        self.assertFalse(tracker.busy())


class BrewChainTestCase(SimpleTestCase):
    """n cups of one recipe in a row (policy.BrewChain): the next once the machine is ready again,
    the rest given up when one fails or the machine stays blocked."""

    def chain(self, n=3):
        return policy.BrewChain([f"r{i}" for i in range(1, n + 1)], 3, {"strength": 7}, "app")

    def done(self, rid):
        return [{"type": "brew_done", "request_id": rid}]

    def test_the_cups_follow_each_other_once_ready(self):
        chain = self.chain()
        self.assertEqual(chain.cup("r2"), (2, 3))
        self.assertEqual(chain.step([], True, False, False, False, 0), [])  # the first one runs
        self.assertEqual(chain.step(self.done("r1"), False, True, False, False, 10), [])
        self.assertEqual(chain.step([], False, True, False, False, 12), [])  # idle, not ready yet
        self.assertEqual(chain.step([], False, True, True, False, 16), [("start", "r2")])
        self.assertEqual(chain.step([], True, False, False, False, 17), [])
        chain.step(self.done("r2"), False, True, False, False, 40)
        self.assertEqual(chain.step([], False, True, False, False, 40 + policy.READY_FALLBACK), [("start", "r3")])
        chain.step(self.done("r3"), False, True, True, False, 80)
        self.assertEqual(chain.step([], False, True, True, False, 90), [])
        self.assertTrue(chain.over)

    def test_the_e6_stays_done_between_the_cups(self):
        """What the E6 sent on 2026-11-23: after cup 1 the phase stays "done" (never "idle") until the
        next command, and "coffee_ready" comes back 30 s later - that is when cup 2 has to start."""
        chain = self.chain(2)
        at_rest = policy.at_rest("done")
        self.assertEqual(chain.step(self.done("r1"), False, at_rest, False, False, 0), [])
        self.assertEqual(chain.step([], False, at_rest, False, False, 20), [])
        self.assertEqual(chain.step([], False, at_rest, True, False, 30), [("start", "r2")])
        self.assertFalse(policy.at_rest("dispensing"))

    def test_a_failed_cup_ends_the_chain(self):
        chain = self.chain()
        chain.step(self.done("r1"), False, True, True, False, 10)
        chain.step([], False, True, True, False, 20)
        actions = chain.step([{"type": "brew_failed", "request_id": "r2", "reason": "stalled"}], False, True, True, False, 30)
        self.assertEqual(actions, [("fail", "r3", "previous cup failed: stalled")])
        self.assertTrue(chain.over)
        self.assertEqual(chain.lost(), [])

    def test_blocked_waits_then_gives_up(self):
        chain = self.chain(2)
        chain.step(self.done("r1"), False, True, True, False, 0)
        self.assertEqual(chain.step([], False, True, True, True, 30), [])  # tank empty - refilled in time?
        self.assertEqual(chain.step([], False, True, True, True, policy.READY_TIMEOUT), [("fail", "r2", "blocked")])

    def test_a_lost_connection_fails_what_wasnt_made(self):
        chain = self.chain()
        self.assertEqual(chain.lost(), [("fail", "r2", "disconnected"), ("fail", "r3", "disconnected")])


class LockTestCase(SimpleTestCase):
    def test_locks_unlocks_and_waits(self):
        self.assertTrue(policy.needs_lock(True, [], -100, 0))
        self.assertIsNone(policy.needs_lock(True, ["lockedkeys"], -100, 0))
        self.assertIsNone(policy.needs_lock(True, [], -5, 0))  # sent 5 s ago: the machine takes ~3 s
        self.assertFalse(policy.needs_lock(False, ["lockedkeys"], -100, 0))
        self.assertIsNone(policy.needs_lock(False, [], -100, 0))


class HomeAssistantTestCase(SimpleTestCase):
    def test_discovery(self):
        state = {"address": "F4:57:52:42:0C:04", "model": "E6 (EB)", "firmware": "EF532M V02.03"}
        products = [{"code": 2, "name": "Espresso"}, {"code": 13, "name": "Hot Water"}]
        configs = dict(ha.configs("coffeemachine", "homeassistant", state, products, brew_buttons=False))
        node = "coffeepy_machine_f4_57_52_42_0c_04"
        water = json.loads(configs[f"homeassistant/binary_sensor/{node}/fill_water/config"])
        self.assertEqual(water["availability_topic"], "coffeemachine/status")
        self.assertEqual(water["device"]["model"], "E6 (EB)")
        self.assertIn("fill_water", water["value_template"])
        count = json.loads(configs[f"homeassistant/sensor/{node}/count_13/config"])
        self.assertEqual((count["state_topic"], count["state_class"]), ("coffeemachine/counters", "total_increasing"))
        self.assertEqual(len({json.loads(c)["unique_id"] for c in configs.values()}), len(configs))
        self.assertFalse([t for t in configs if "/button/" in t])  # brewing from HA: off unless asked for

    def test_brew_buttons_count_as_brews_at_the_machine(self):
        configs = dict(ha.configs("cm", "ha", {}, [{"code": 2, "name": "Espresso"}], brew_buttons=True))
        button = json.loads(next(c for t, c in configs.items() if "/button/" in t))
        self.assertEqual(json.loads(button["payload_press"]), {"action": "brew", "product": 2, "source": "ha"})
        self.assertEqual(button["command_topic"], "cm/cmd")


class ConnectTimeoutTestCase(SimpleTestCase):
    """A connect that never finishes ends the session (bridge.CONNECT_TIMEOUT) - on 2026-11-23 one waited five
    hours for BlueZ, and the machine stayed "not connected" until the bridge was restarted by hand."""

    def test_a_hanging_connect_is_given_up(self):
        import asyncio  # noqa: PLC0415
        import types  # noqa: PLC0415
        from unittest import mock  # noqa: PLC0415

        sys.path.insert(0, str(settings.BASE_DIR / "coffee-machine"))
        try:
            import bridge  # noqa: PLC0415 - paho, no bleak
        finally:
            sys.path.pop(0)

        class Hanging:
            def __init__(self, address):
                self.address = address

            async def connect(self):
                await asyncio.Event().wait()

        machine = bridge.Bridge()
        machine.client = mock.Mock()
        with mock.patch.object(bridge, "CONNECT_TIMEOUT", 0.05):
            with self.assertRaises(TimeoutError):
                asyncio.run(machine.session(types.SimpleNamespace(JuraBle=Hanging)))
        self.assertFalse(machine.state["connected"])
        self.assertIn("no connection within", machine.state["error"])

    def test_since_when_the_machine_is_gone(self):
        """Switched off is "not found" (drivers.jura_ble.NotFound): since when goes into the state - the kiosk's
        night follows it - and only a connection clears it."""
        import asyncio  # noqa: PLC0415
        import types  # noqa: PLC0415
        from unittest import mock  # noqa: PLC0415

        sys.path.insert(0, str(settings.BASE_DIR / "coffee-machine"))
        try:
            import bridge  # noqa: PLC0415
        finally:
            sys.path.pop(0)

        class Gone(Exception):
            missing = True

        outcome = {"exc": Gone("F4:57:52:42:0C:04 not found")}

        class Machine:
            def __init__(self, address):
                pass

            async def connect(self):
                raise outcome["exc"]

        machine = bridge.Bridge()
        machine.client = mock.Mock()
        module = types.SimpleNamespace(JuraBle=Machine)
        with mock.patch.object(bridge.time, "time", return_value=1000.0), self.assertRaises(Gone):
            asyncio.run(machine.session(module))
        self.assertEqual(machine.state["missing_since"], 1000.0)
        with mock.patch.object(bridge.time, "time", return_value=2000.0), self.assertRaises(Gone):
            asyncio.run(machine.session(module))
        self.assertEqual(machine.state["missing_since"], 1000.0)  # still since the first time
        outcome["exc"] = RuntimeError("Operation already in progress")  # the radio, not the machine
        with self.assertRaises(RuntimeError):
            asyncio.run(machine.session(module))
        self.assertEqual(machine.state["missing_since"], 1000.0)

        class Back(Machine):  # switched on again: connects; the test ends the session right after
            article = 15377
            connected = False  # what the session's cleanup asks

            async def connect(self):
                pass

            async def about(self):
                return {"firmware": "EF532M V02.03", "bluefrog": "V05.08F"}

            def products(self, counted):
                raise RuntimeError("enough")

            async def disconnect(self):
                pass

        fake_py_jura = types.SimpleNamespace(machines=types.SimpleNamespace(ARTICLE_NAMES={15377: "E6 (EB)"}))
        with mock.patch.dict(sys.modules, {"py_jura": fake_py_jura, "py_jura.machines": fake_py_jura.machines}):
            with self.assertRaises(RuntimeError):
                asyncio.run(machine.session(types.SimpleNamespace(JuraBle=Back)))
        self.assertIsNone(machine.state["missing_since"])  # and stays so when that session ends
