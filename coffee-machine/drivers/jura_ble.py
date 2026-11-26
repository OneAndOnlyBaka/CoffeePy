"""A JURA over Bluetooth LE - its BlueFrog, built in or the Smart Connect dongle on the service port.

py-jura supplies the cipher, the UUIDs, the command builders and the machine definitions; the
connection and the reads are done here, because three things py-jura 0.2.1 gets wrong on an E6
(measured 2026-11-06, see codec.py): the statistics' "ready" byte, the key (it is not fixed, so
every connect reads it from a fresh advertisement), and that some advertisements come without the
manufacturer data. There is also no working cancel - the keys are locked instead (policy.py).

Never writes to the UART tunnel (5a401625): a write it didn't expect made the dongle change its
key and refuse access for a while. Reading it is harmless, but nothing here needs it yet."""
import asyncio
import logging

from bleak import BleakClient, BleakScanner
from bleak_retry_connector import establish_connection
from py_jura import machines
from py_jura.encoder import encdec
from py_jura.products import Product, Temperature
from py_jura.protocol import (
    ABOUT_MACHINE_UUID, BARISTA_MODE_UUID, JURA_MANUFACTURER_ID, MACHINE_STATUS_UUID, P_MODE_UUID,
    PRODUCT_PROGRESS_UUID, START_PRODUCT_UUID, STATISTICS_COMMAND_UUID, STATISTICS_DATA_UUID, StatMode,
    build_brew_command, build_disconnect_command, build_heartbeat_command, build_lock_command,
    build_stats_command, build_unlock_command,
)

import codec

log = logging.getLogger("coffee-machine.jura")

HEARTBEAT_SECONDS = 9  # py-jura's; without it the BlueFrog drops the connection
SCAN_ATTEMPTS = 4  # each up to SCAN_SECONDS: one advertisement in a few lacks the key
SCAN_SECONDS = 10
# After this many failed connects in a row, the machine is removed from BlueZ and found afresh. Measured
# 2026-11-23 on a Pi 3's own Bluetooth: after a garbled read the link died, BlueZ kept saying "connected", and
# every connect failed with "Not connected" for 12 minutes - until the device was removed by hand.
# (bleak-retry-connector's close_stale_connections_by_address before every connect was tried too: it tore
# the new connection down 2-3 s after it came up, every time - bluetoothd "No matching connection".)
FORGET_AFTER = 3
READ_TIMEOUT = 10.0  # one GATT read or D-Bus call; nothing here may wait for ever (bridge.CONNECT_TIMEOUT)
_failed_connects = 0  # module-level: every session makes a new JuraBle
STATS_TIMEOUT = 10.0


class NotConnected(Exception):
    pass


class NotFound(NotConnected):
    """No advertisement at all: the machine is switched off (bridge.py notes since when)."""
    missing = True


def product_name(code: int, definition=None) -> str:
    known = definition.products.get(Product(code)) if definition and code in Product._value2member_map_ else None
    if known is not None:
        return known.name
    if code in Product._value2member_map_:
        return Product(code).name.replace("_", " ").title()
    return f"Product {code:#04x}"


def strokes(code: int) -> int:
    """Tally strokes a product is worth by default: two-cup products two (the admin can change it)."""
    return 2 if code in Product._value2member_map_ and Product(code).name.startswith("TWO_") else 1


async def scan(seconds=SCAN_SECONDS) -> list[dict]:
    """Every JURA advertising nearby - what the dashboard offers to connect to."""
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    machines_seen = []
    for address, (dev, adv) in found.items():
        parsed = codec.advertisement(adv.manufacturer_data.get(JURA_MANUFACTURER_ID, b""))
        if JURA_MANUFACTURER_ID not in adv.manufacturer_data and "BlueFrog" not in (dev.name or ""):
            continue
        article = parsed[1] if parsed else None
        machines_seen.append({
            "address": address, "name": dev.name or "", "rssi": adv.rssi, "article": article,
            "model": machines.ARTICLE_NAMES.get(article, "") if article else "",
        })
    return sorted(machines_seen, key=lambda m: -(m["rssi"] or -999))


def recipe(known) -> dict:
    """What a brew of this product may ask for (README, `products`): the definition's ranges and
    defaults - CoffeePy's "my coffees" slots stay within them. Temperatures as their wire values
    (0 low, 1 normal, 2 high)."""
    out = {}
    if known.strength is not None:
        values = known.strength.values
        out["strength"] = {"min": min(values), "max": max(values), "default": known.strength.default}
    if known.water is not None:
        w = known.water
        out["water"] = {"min": w.min, "max": w.max, "step": w.step, "default": w.default}
    if known.temperature is not None:
        out["temperature"] = {"options": [t.value for t in known.temperature.options],
                              "default": known.temperature.default.value}
    return out


class JuraBle:
    def __init__(self, address: str):
        self.address = address.upper()
        self.client = None
        self.key = None
        self.article = None
        self.definition = None
        self.lost = asyncio.Event()
        self._heartbeat = None
        self._lock = asyncio.Lock()  # one GATT exchange at a time: the statistics take several
        # The last frames that decoded soundly (codec.sound) - what a garbled read is answered with.
        self._progress = {"state": 0, "phase": "idle", "product": 0}
        self._alerts = []
        self._status_hex = ""
        self._frames = {}  # uuid: the last frame believed

    # --- connection -------------------------------------------------------------------------
    async def connect(self):
        global _failed_connects
        try:
            await self._connect(found := {})
        except NotConnected:
            raise  # not found, or no key: switched off or asleep - the normal case at night, nothing to repair
        except Exception:
            if found.get("device") is not None:  # there, and still no connection: BlueZ is stuck on it
                _failed_connects += 1
                if _failed_connects >= FORGET_AFTER:
                    _failed_connects = 0
                    await forget(found["device"])
            raise
        _failed_connects = 0

    async def _connect(self, found):
        device, data = None, None
        for _ in range(SCAN_ATTEMPTS):
            device, data = await self._find()
            found["device"] = device or found.get("device")
            if device is not None and data is not None:
                break
        if device is None:
            raise NotFound(f"{self.address} not found")
        if data is None:
            raise NotConnected(f"{self.address} advertises no key")
        self.key, self.article = data
        self.definition = machines.MACHINES.get(self.article)
        self.lost.clear()
        self.client = await establish_connection(BleakClient, device, self.address,
                                                 disconnected_callback=lambda _: self.lost.set(), max_attempts=3)
        await self._write(P_MODE_UUID, build_heartbeat_command(self.key), response=False)
        self._heartbeat = asyncio.create_task(self._beat())
        log.info("connected to %s (article %s, %s)", self.address, self.article,
                 self.definition.name if self.definition else "no machine definition")

    async def _find(self):
        result = {}

        def seen(dev, adv):
            if dev.address.upper() != self.address:
                return
            result["device"] = dev
            parsed = codec.advertisement(adv.manufacturer_data.get(JURA_MANUFACTURER_ID, b""))
            if parsed:
                result["data"] = parsed

        async with BleakScanner(detection_callback=seen):
            for _ in range(SCAN_SECONDS * 10):
                if "data" in result:
                    break
                await asyncio.sleep(0.1)
        return result.get("device"), result.get("data")

    async def _beat(self):
        try:
            while self.client is not None and self.client.is_connected:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                await self._write(P_MODE_UUID, build_heartbeat_command(self.key), response=False)
        except Exception as exc:  # noqa: BLE001 - a lost connection; the bridge notices via `lost`
            log.warning("heartbeat failed: %s", exc)
            self.lost.set()

    async def disconnect(self):
        if self._heartbeat:
            self._heartbeat.cancel()
        if self.client is not None and self.client.is_connected:
            try:
                await self._write(P_MODE_UUID, build_disconnect_command(self.key), response=False)
                await self.client.disconnect()
            except Exception as exc:  # noqa: BLE001 - it is going anyway
                log.info("disconnect: %s", exc)
        self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected and not self.lost.is_set()

    # --- reads --------------------------------------------------------------------------------
    async def _read(self, uuid) -> bytes:
        if not self.connected:
            raise NotConnected("not connected")
        return encdec(bytes(await asyncio.wait_for(self.client.read_gatt_char(uuid), READ_TIMEOUT)), self.key)

    async def _write(self, uuid, data, response=True):
        await self.client.write_gatt_char(uuid, data, response=response)

    async def about(self) -> dict:
        raw = bytes(await asyncio.wait_for(self.client.read_gatt_char(ABOUT_MACHINE_UUID), READ_TIMEOUT))  # plain text
        text = lambda b: b.replace(b"\x00", b"").decode("ascii", "replace").strip()  # noqa: E731
        return {"bluefrog": text(raw[27:35]), "firmware": text(raw[35:51])}

    async def _believed(self, uuid, name) -> bytes | None:
        """A frame read and - if it changed - read again to confirm it (codec.confirmed); None if not."""
        async with self._lock:
            first = await self._read(uuid)
            previous = self._frames.get(uuid)
            second = None if first == previous else await self._read(uuid)
        frame = codec.confirmed(previous, first, second) if codec.sound(first, self.key) else None
        if frame is None:
            log.warning("%s frame not believed: %s, then %s", name, first.hex(), second.hex() if second else "-")
            return None
        self._frames[uuid] = frame
        return frame

    async def progress(self) -> dict:
        frame = await self._believed(PRODUCT_PROGRESS_UUID, "progress")
        if frame is not None:
            self._progress = codec.progress(frame)
        return self._progress

    async def alerts(self) -> list[dict]:
        frame = await self._believed(MACHINE_STATUS_UUID, "status")
        if frame is None:
            return self._alerts
        if frame.hex() != self._status_hex:  # rare, and what the next unknown bit is found from
            self._status_hex = frame.hex()
            log.info("status frame %s", self._status_hex)
        known = self.definition.alerts if self.definition else {}
        self._alerts = [{"bit": b, "key": known[b].key if b in known else f"bit_{b}",
                         "blocking": bool(known[b].blocking) if b in known else False}
                        for b in codec.alert_bits(frame)]
        return self._alerts

    async def _stats(self, mode) -> bytes:
        async with self._lock:
            await self._write(STATISTICS_COMMAND_UUID, build_stats_command(self.key, mode))
            seen_busy = False
            loop = asyncio.get_running_loop()
            deadline = loop.time() + STATS_TIMEOUT
            while loop.time() < deadline:
                await asyncio.sleep(0.3)
                first = (await self._read(STATISTICS_COMMAND_UUID))[0]
                # The E6 answers 0x0e and stays there; py-jura's E8 goes to "not the key" and back.
                if first == codec.STATS_READY or (seen_busy and first == self.key):
                    return await self._read(STATISTICS_DATA_UUID)
                seen_busy = seen_busy or first != self.key
            raise TimeoutError(f"statistics mode {mode} not answered")

    async def counters(self) -> dict:
        result = codec.counters(await self._stats(StatMode.PRODUCT_COUNTERS))
        names = (self.definition.maintenance_counter_types, self.definition.maintenance_percent_types) \
            if self.definition else ((), ())
        if names[0]:
            result["maintenance"] = codec.maintenance(await self._stats(StatMode.MAINTENANCE_COUNTER),
                                                      await self._stats(StatMode.MAINTENANCE_PERCENT), *names)
        return result

    def products(self, counted=()) -> list[dict]:
        """What the machine makes: its definition's products, plus every code its counters count
        (the E6's hot water isn't in py-jura's file but in its counters)."""
        codes = {p.value for p in self.definition.products} if self.definition else set()
        codes |= {c for c in counted if c}
        out = []
        for code in sorted(codes):
            item = {"code": code, "name": product_name(code, self.definition), "strokes": strokes(code)}
            known = self.definition.products.get(Product(code)) if self.definition and code in Product._value2member_map_ else None
            if known is not None:
                if known.water is not None:
                    item["water_ml"] = known.water.default
                if known.strength is not None:
                    item["strength"] = known.strength.default
                item["brewable"] = True
                item["recipe"] = recipe(known)
            else:
                item["brewable"] = False  # no definition: its parameters would be guesses
            out.append(item)
        return out

    # --- commands -----------------------------------------------------------------------------
    async def brew(self, code: int, options: dict | None = None):
        """Starts a product with the definition's defaults, or `options` (water_ml, strength,
        temperature) where given. A product without a definition only with water_ml: an all-zero
        recipe is not something to send to a machine."""
        options = options or {}
        known = self.definition.products.get(Product(code)) if self.definition and code in Product._value2member_map_ else None
        if known is not None:
            temperature = options.get("temperature")
            command = build_brew_command(known, self.key, strength=options.get("strength"),
                                         water_ml=options.get("water_ml"),
                                         temperature=Temperature(temperature) if temperature is not None else None)
        elif options.get("water_ml"):
            data = bytearray(18)
            data[0] = data[17] = self.key
            data[1] = code
            data[4] = int(options["water_ml"]) // 5
            data[7] = int(options.get("temperature", Temperature.NORMAL.value))
            if options.get("strength"):
                data[3] = int(options["strength"])
            command = encdec(bytes(data), self.key)
        else:
            raise ValueError(f"no recipe for product {code:#04x}")
        async with self._lock:
            await self._write(START_PRODUCT_UUID, command)

    async def lock(self, locked: bool):
        async with self._lock:
            await self._write(BARISTA_MODE_UUID, (build_lock_command if locked else build_unlock_command)(self.key))




async def forget(device):
    """Removes the machine from BlueZ (Adapter1.RemoveDevice, what `bluetoothctl remove` does): a link BlueZ
    still holds for connected goes with it, and the next scan finds the machine as new. Only for a machine that
    is there - one switched off is simply looked for again. (Switching the adapter off and on as well was tried
    on 2026-11-23: on a Pi 3's own Bluetooth the "on" failed once and left the chip hanging until its driver was
    bound again by hand.)"""
    from dbus_fast import BusType, Message  # noqa: PLC0415 - bleak's own D-Bus library, Linux only
    from dbus_fast.aio import MessageBus  # noqa: PLC0415

    path = (getattr(device, "details", None) or {}).get("path") or ""
    if "/dev_" not in path:
        return
    bus = await asyncio.wait_for(MessageBus(bus_type=BusType.SYSTEM).connect(), READ_TIMEOUT)
    try:
        reply = await asyncio.wait_for(bus.call(Message(
            destination="org.bluez", path=path.rsplit("/", 1)[0], interface="org.bluez.Adapter1",
            member="RemoveDevice", signature="o", body=[path])), READ_TIMEOUT)
        log.warning("removed %s from BlueZ after %s failed connects: %s", path, FORGET_AFTER, reply.message_type.name)
    finally:
        bus.disconnect()
