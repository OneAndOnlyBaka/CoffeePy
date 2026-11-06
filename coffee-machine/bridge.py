"""Coffee machine <-> MQTT bridge: keeps one machine connected, publishes what it reports, runs the
brews it is asked for and locks its keys if manual brews are to be blocked. The MQTT contract is in
README.md - CoffeePy's mqtt-listener is one client of it, Home Assistant (ha.py) can be another.

Without a configured machine it does nothing but answer on MQTT ("disabled") - a device without a
coffee machine, or with one CoffeePy doesn't talk to, just has this container idle.

Configuration comes from the environment and is replaced by the retained `<prefix>/config` that
CoffeePy publishes (dashboard -> Coffee machine), so the same image runs with CoffeePy or on its own."""
import asyncio
import json
import logging
import os
import time
import uuid

import paho.mqtt.client as mqtt

import ha
import policy
from drivers import load, machine_class

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coffee-machine")

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "coffee-machine-bridge")
PREFIX = os.environ.get("MACHINE_TOPIC", "coffeemachine")
HA_DISCOVERY = os.environ.get("HA_DISCOVERY", "false").lower() == "true"
HA_PREFIX = os.environ.get("HA_DISCOVERY_PREFIX", "homeassistant")
HA_BREW = os.environ.get("HA_BREW", "false").lower() == "true"
# A pretend machine whatever driver the config names (drivers/simulator.py) - for trying the kiosk
# without one in reach. Never set on a device in service.
SIMULATE = os.environ.get("MACHINE_SIMULATE", "false").lower() == "true"

PROGRESS_SECONDS = 1.0
ALERT_SECONDS = 5.0
COUNTER_SECONDS = 15 * 60
COUNTERS_AFTER_BREW = 5.0  # the machine counts a product a few seconds after it ended
RECONNECT_BACKOFF = (5, 10, 30, 60)


def env_config() -> dict:
    return {
        "driver": os.environ.get("MACHINE_DRIVER", "none"),
        "address": os.environ.get("MACHINE_ADDRESS", ""),
        "block_manual": os.environ.get("BLOCK_MANUAL", "false").lower() == "true",
    }


class Bridge:
    def __init__(self):
        self.loop = None
        self.inbox = asyncio.Queue()
        self.config = env_config()
        self.config_changed = asyncio.Event()
        self.state = {"connected": False, "alerts": [], "blocking": [], "locked": False,
                      "progress": {"phase": "idle", "product": 0}, "error": ""}
        self.products = []
        self.counted = set()
        self.status = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        self.client.will_set(f"{PREFIX}/status", "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # --- MQTT (paho's thread: everything is handed to the asyncio loop) ----------------------
    def _on_connect(self, client, userdata, flags, reason, properties=None):
        log.info("MQTT connected (%s)", reason)
        client.subscribe([(f"{PREFIX}/config", 1), (f"{PREFIX}/cmd", 1)])
        if self.status:
            self.publish("status", self.status, retain=True, raw=True)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            log.warning("ignoring non-JSON message on %s", msg.topic)
            return
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, (msg.topic.rsplit("/", 1)[-1], payload))

    def publish(self, topic, payload, retain=False, raw=False):
        self.client.publish(f"{PREFIX}/{topic}", payload if raw else json.dumps(payload), qos=1, retain=retain)

    def set_status(self, status):
        if status != self.status:
            self.status = status
            self.publish("status", status, retain=True, raw=True)

    def publish_state(self, **changes):
        new = {**self.state, **changes}
        if new != self.state or changes.get("force"):
            new.pop("force", None)
            self.state = new
            self.publish("state", {**new, **{k: self.config[k] for k in ("driver", "address", "block_manual")},
                                   "updated": time.time()}, retain=True)

    def event(self, event):
        log.info("event %s", event)
        self.publish("event", {**event, "time": time.time()})

    # --- the inbox ------------------------------------------------------------------------------
    async def handle(self, machine):
        """Works through what arrived; returns when the inbox is empty."""
        while not self.inbox.empty():
            kind, payload = self.inbox.get_nowait()
            if kind == "config":
                new = {**env_config(), **{k: payload[k] for k in ("driver", "address", "block_manual") if k in payload}}
                if new != self.config:
                    log.info("config: %s", new)
                    changed_machine = (new["driver"], new["address"]) != (self.config["driver"], self.config["address"])
                    self.config = new
                    if changed_machine:
                        self.config_changed.set()
            elif kind == "cmd":
                await self.command(machine, payload)

    async def command(self, machine, cmd):
        action = cmd.get("action")
        request_id = cmd.get("id") or uuid.uuid4().hex
        if action == "scan":
            await self.scan(request_id)
        elif action == "refresh" and machine is not None and machine.connected:
            await self.read_counters(machine)
        elif action == "brew":
            await self.brew(machine, request_id, cmd)
        elif action == "simulate" and machine is not None and hasattr(machine, "simulate"):
            machine.simulate(cmd.get("alerts") or [])

    async def scan(self, request_id):
        driver = "simulator" if SIMULATE else (self.config["driver"] if self.config["driver"] != "none" else "jura_ble")
        try:
            devices = await load(driver).scan()
            # A connected BlueFrog doesn't advertise - it would be missing from its own scan.
            if self.state["connected"] and not any(d["address"].upper() == self.config["address"].upper() for d in devices):
                devices.insert(0, {"address": self.config["address"], "name": "", "rssi": None,
                                   "article": self.state.get("article"), "model": self.state.get("model", ""),
                                   "connected": True})
            self.publish("scan", {"id": request_id, "devices": devices, "error": ""})
        except Exception as exc:  # noqa: BLE001 - reported to whoever asked
            log.warning("scan failed: %s", exc)
            self.publish("scan", {"id": request_id, "devices": [], "error": str(exc)})

    async def brew(self, machine, request_id, cmd):
        source = cmd.get("source") or policy.SOURCE_APP
        product = int(cmd.get("product") or 0)
        failed = {"type": "brew_failed", "request_id": request_id, "source": source, "product": product}
        if source == "ha" and not HA_BREW:
            self.event({**failed, "reason": "brewing from Home Assistant is off"})
        elif machine is None or not machine.connected:
            self.event({**failed, "reason": "disconnected"})
        elif self.tracker.busy():
            self.event({**failed, "reason": "busy"})
        elif self.state["blocking"]:
            self.event({**failed, "reason": "blocked", "alerts": self.state["blocking"]})
        else:
            try:
                await machine.brew(product, cmd.get("options"))
            except Exception as exc:  # noqa: BLE001 - reported to whoever asked
                self.event({**failed, "reason": str(exc)})
                return
            self.tracker.expect(request_id, product, time.monotonic(), source=source)

    # --- the machine ------------------------------------------------------------------------------
    async def read_counters(self, machine):
        try:
            counters = await machine.counters()
        except Exception as exc:  # noqa: BLE001 - next round
            log.warning("counters: %s", exc)
            return
        self.publish("counters", {**counters, "taken": time.time()}, retain=True)
        counted = {c for c, n in counters["products"].items() if n}
        if counted - self.counted:
            self.counted |= counted
            await self.publish_products(machine)

    async def publish_products(self, machine):
        self.products = machine.products(self.counted)
        self.publish("products", {"article": machine.article, "model": self.state.get("model", ""),
                                  "products": self.products}, retain=True)
        if HA_DISCOVERY:
            for topic, payload in ha.configs(PREFIX, HA_PREFIX, {**self.state, "address": self.config["address"]},
                                             self.products, HA_BREW):
                self.client.publish(topic, payload, qos=1, retain=True)

    async def session(self, mod):
        machine = machine_class(mod)(self.config["address"])
        self.tracker = policy.BrewTracker()
        try:
            await machine.connect()
        except Exception as exc:  # noqa: BLE001 - retried with a backoff
            self.publish_state(connected=False, error=str(exc))
            self.set_status("offline")
            raise
        try:
            about = await machine.about()
            from py_jura import machines as known  # noqa: PLC0415
            self.publish_state(connected=True, error="", article=machine.article, firmware=about["firmware"],
                               bluefrog=about["bluefrog"], model=known.ARTICLE_NAMES.get(machine.article, ""))
            self.set_status("online")
            await self.publish_products(machine)
            loop = asyncio.get_running_loop()
            # The messages first: reading the counters takes the machine ~20 s (three statistics).
            next_alerts, next_counters = 0.0, loop.time() + ALERT_SECONDS
            counters_due = None
            lock_sent = -policy.RELOCK_SECONDS
            while machine.connected and not self.config_changed.is_set():
                await self.handle(machine)
                now = loop.time()
                prog = await machine.progress()
                for event in self.tracker.update(prog, time.monotonic()):
                    self.event(event)
                    if event["type"] == "brew_done":
                        counters_due = now + COUNTERS_AFTER_BREW
                public = {k: v for k, v in prog.items() if k != "state"}
                if now >= next_alerts:
                    alerts = await machine.alerts()
                    keys = [a["key"] for a in alerts]
                    decision = policy.needs_lock(self.config["block_manual"], keys, lock_sent, now)
                    if decision is not None:
                        await machine.lock(decision)
                        lock_sent = now
                    self.publish_state(progress=public, alerts=keys, locked="lockedkeys" in keys,
                                       blocking=[a["key"] for a in alerts if a["blocking"]])
                    next_alerts = now + ALERT_SECONDS
                else:
                    self.publish_state(progress=public)
                if now >= next_counters or (counters_due is not None and now >= counters_due):
                    await self.read_counters(machine)
                    next_counters, counters_due = now + COUNTER_SECONDS, None
                await asyncio.sleep(PROGRESS_SECONDS)
        finally:
            for event in self.tracker.lost():
                self.event(event)
            # A lock the machine no longer needs goes with the session; one that is still wanted
            # stays - the machine drops it by itself shortly after the connection (~40 s).
            if machine.connected and self.state.get("locked") and not self.config["block_manual"]:
                try:
                    await machine.lock(False)
                except Exception as exc:  # noqa: BLE001 - it drops by itself
                    log.info("unlock on the way out: %s", exc)
            await machine.disconnect()
            self.publish_state(connected=False, progress={"phase": "idle", "product": 0})

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.tracker = policy.BrewTracker()
        while True:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
                break
            except OSError as exc:
                log.warning("MQTT connect failed: %s - retrying in 5s", exc)
                await asyncio.sleep(5)
        self.client.loop_start()
        failures = 0
        while True:
            self.config_changed.clear()
            await self.handle(None)
            if self.config["driver"] == "none" or not self.config["address"]:
                self.set_status("disabled")
                self.publish_state(connected=False, error="", force=True)
                await self._idle(None)
                continue
            try:
                await self.session(load("simulator" if SIMULATE else self.config["driver"]))
                failures = 0
            except Exception as exc:  # noqa: BLE001 - logged, retried
                log.warning("machine session ended: %s", exc)
                failures += 1
            self.set_status("offline")
            if not self.config_changed.is_set():
                await self._idle(RECONNECT_BACKOFF[min(failures, len(RECONNECT_BACKOFF) - 1)])

    async def _idle(self, seconds):
        """Waits for a config change (or `seconds`), answering commands meanwhile."""
        deadline = None if seconds is None else self.loop.time() + seconds
        while not self.config_changed.is_set() and (deadline is None or self.loop.time() < deadline):
            await self.handle(None)
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(Bridge().run())
