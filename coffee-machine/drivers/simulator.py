"""A pretend JURA E6 - for trying the kiosk without a machine in reach (the test Pi, the manual's
screenshots, the product video). Same interface as jura_ble.JuraBle; a brew runs through the phases
the real E6 showed (heating, starting, dispensing, done) on a shortened clock, and the machine's
messages are set with a `simulate` command: {"action": "simulate", "alerts": ["fill_water"]}.

Only with MACHINE_SIMULATE=true in the bridge's environment - never chosen from the dashboard."""
import asyncio
import time

import codec

BLOCKING = {"fill_water", "empty_grounds", "empty_tray", "insert_tray", "error_status"}
PRODUCTS = [  # the E6's, as the bridge lists them for the real one
    {"code": 2, "name": "Espresso", "strokes": 1, "water_ml": 45, "strength": 6, "brewable": True},
    {"code": 3, "name": "Coffee", "strokes": 1, "water_ml": 100, "strength": 4, "brewable": True},
    {"code": 4, "name": "Cappuccino", "strokes": 1, "water_ml": 60, "strength": 6, "brewable": True},
    {"code": 8, "name": "Milk Foam", "strokes": 1, "brewable": True},
    {"code": 13, "name": "Hot Water", "strokes": 1, "brewable": False},
    {"code": 19, "name": "2 Coffee", "strokes": 2, "water_ml": 200, "strength": 4, "brewable": True},
]
# (phase state, seconds) - the real E6 heated ~4 s (30 s out of energy saving) and poured ~25 s.
TIMELINE = ((codec.STATE_HEATING, 4), (codec.STATE_STARTING, 2), (codec.STATE_DISPENSING, 10), (codec.STATE_DONE, 3))


async def scan(seconds=3):
    await asyncio.sleep(min(seconds, 3))
    return [{"address": "00:00:00:00:00:E6", "name": "Simulated BlueFrog", "rssi": -50, "article": 15377,
             "model": "E6 (EB) - simulated"}]


class Simulator:
    def __init__(self, address):
        self.address = address
        self.article = 15377
        self.lost = asyncio.Event()
        self._alerts = ["coffee_ready"]
        self.locked = False
        self.brewing = None  # (code, started, water_ml, strength)
        self.counted = {2: 1192, 3: 2641, 4: 311, 8: 20, 13: 29, 19: 12}

    async def connect(self):
        await asyncio.sleep(1)

    async def disconnect(self):
        pass

    @property
    def connected(self):
        return not self.lost.is_set()

    async def about(self):
        return {"bluefrog": "SIM", "firmware": "EF532M simulated"}

    async def progress(self):
        if self.brewing is None:
            return {"state": codec.STATE_IDLE, "phase": "idle", "product": 0}
        code, started, water, strength = self.brewing
        elapsed = time.monotonic() - started
        for state, seconds in TIMELINE:
            if elapsed < seconds:
                result = {"state": state, "phase": codec.PHASES[state], "product": code}
                if state in (codec.STATE_STARTING, codec.STATE_DISPENSING):
                    result.update(strength=strength, water_ml=water, temperature=1)
                if state == codec.STATE_DISPENSING:
                    result["percent"] = int(elapsed * 100 / seconds)
                return result
            elapsed -= seconds
        self.counted[code] = self.counted.get(code, 0) + 1
        self.brewing = None
        return {"state": codec.STATE_IDLE, "phase": "idle", "product": 0}

    async def alerts(self):
        keys = list(self._alerts) + (["lockedkeys"] if self.locked else [])
        return [{"bit": 0, "key": k, "blocking": k in BLOCKING} for k in keys]

    async def counters(self):
        return {"total": sum(self.counted.values()), "products": dict(self.counted),
                "maintenance": {"counters": {"cleaning": 37, "filter_change": 16, "decalc": 3},
                                "percent": {"cleaning": 40, "filter_change": 7, "decalc": 92}}}

    def products(self, counted=()):
        return [dict(p) for p in PRODUCTS]

    async def brew(self, code, options=None):
        if any(k in BLOCKING for k in self._alerts):
            raise RuntimeError("blocked")
        product = next((p for p in PRODUCTS if p["code"] == code), None)
        if product is None or (not product["brewable"] and not (options or {}).get("water_ml")):
            raise ValueError(f"no recipe for product {code:#04x}")
        water = (options or {}).get("water_ml") or product.get("water_ml") or 0
        self.brewing = (code, time.monotonic(), water, (options or {}).get("strength") or product.get("strength"))

    async def lock(self, locked):
        self.locked = locked

    def simulate(self, alerts):
        self._alerts = [str(a) for a in alerts]
