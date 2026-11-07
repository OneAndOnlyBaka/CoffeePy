"""What the bridge makes of the progress it polls: which brew is whose, when it ended, and whether
the keys have to be locked again. Pure - fed with decoded progress and the time, tested without a
machine (coffee/tests/test_machine_bridge.py)."""
import codec

# A brew asked for that doesn't show up in the progress by then failed (the machine refused it -
# blocked, or busy with something else). The E6 took 3 s from the command to the first frame,
# coming out of energy saving it heats for ~30 s first, but that already shows as 0x21.
START_TIMEOUT = 20.0
# A brew whose progress stops changing for this long without reaching "done" is over anyway (the
# machine was switched off, the connection was lost and came back on an idle machine).
STALL_TIMEOUT = 180.0
# How often the lock is re-sent while the machine doesn't report it: it takes ~3 s to show.
RELOCK_SECONDS = 10.0

SOURCE_APP = "app"
SOURCE_MANUAL = "manual"


class BrewTracker:
    """One brew at a time, as the machine has one outlet. `expect` registers a brew this bridge
    asked for; `update` turns progress into events:

        {"type": "brew_started", "request_id", "source", "product"}
        {"type": "brew_done", ... "water_ml", "strength", "temperature"}
        {"type": "brew_failed", "request_id", "product", "reason"}

    A product that starts without having been asked for is the machine's own ("manual") - chosen
    at its keys while they were unlocked, or by anything else that talks to it."""

    def __init__(self):
        self.pending = None  # {"request_id", "product", "since", "source"}
        self.current = None  # the brew that runs, with its recipe once known
        self.changed_at = 0.0
        self.last = None

    def expect(self, request_id, product, now, source=SOURCE_APP):
        self.pending = {"request_id": request_id, "product": product, "since": now, "source": source}

    def busy(self) -> bool:
        return self.current is not None or self.pending is not None

    def update(self, prog: dict, now: float) -> list[dict]:
        events = []
        if prog != self.last:
            self.changed_at = now
            self.last = prog
        active = codec.is_active(prog["state"]) and prog["product"]
        if self.current is None and active:
            pending = self.pending
            mine = pending is not None and pending["product"] == prog["product"]
            self.current = {"request_id": pending["request_id"] if mine else None,
                            "source": pending["source"] if mine else SOURCE_MANUAL,
                            "product": prog["product"], "started": now}
            if mine:
                self.pending = None
            events.append({"type": "brew_started", **self._public()})
        if self.current is not None:
            for key in ("strength", "water_ml", "temperature"):
                if key in prog:
                    self.current[key] = prog[key]
            if prog["state"] == codec.STATE_DONE or not codec.is_active(prog["state"]):
                # Back to idle without "done" in between happens when a frame was missed between
                # two polls - the product was made all the same.
                events.append({"type": "brew_done", **self._public()})
                self.current = None
            elif now - self.changed_at > STALL_TIMEOUT:
                events.append({"type": "brew_failed", **self._public(), "reason": "stalled"})
                self.current = None
        if self.pending is not None and now - self.pending["since"] > START_TIMEOUT:
            events.append({"type": "brew_failed", "request_id": self.pending["request_id"],
                           "source": self.pending["source"], "product": self.pending["product"],
                           "reason": "not started"})
            self.pending = None
        return events

    def lost(self) -> list[dict]:
        """The connection went: a brew asked for and not seen yet failed; one that ran is left for
        the app to decide (it was most likely made - coffee.machine books it after a timeout)."""
        events = []
        if self.pending is not None:
            events.append({"type": "brew_failed", "request_id": self.pending["request_id"],
                           "source": self.pending["source"], "product": self.pending["product"],
                           "reason": "disconnected"})
            self.pending = None
        self.current = None
        self.last = None
        return events

    def _public(self):
        return {k: v for k, v in self.current.items() if k != "started"}


def needs_lock(block_manual: bool, alert_keys, last_sent: float, now: float) -> bool | None:
    """True to lock, False to unlock, None to leave it. The lock lives only as long as the
    connection: lost, it drops by itself within ~40 s (measured) - so it is sent after every
    connect and again whenever the machine stops reporting it."""
    locked = "lockedkeys" in alert_keys
    if now - last_sent < RELOCK_SECONDS:
        return None
    if block_manual and not locked:
        return True
    if not block_manual and locked:
        return False
    return None


# n brews of one recipe in a row, into one big cup or a jug (CoffeePy's "my coffees" slots): the
# next starts once the machine is idle again and says so. The E6 reported "coffee_ready" ~9 s after a
# product ended (2026-11-06); a model that never says it gets the next one after READY_FALLBACK.
READY_PAUSE = 5.0
READY_FALLBACK = 30.0
# Idle and still not ready, or blocked, for this long: the rest of the chain is given up.
READY_TIMEOUT = 120.0


class BrewChain:
    """The cups of one `brew` with `count` > 1, one request id each. `step` is fed what happened
    and says what to do next:

        ("start", request_id)          - brew the next cup now
        ("fail", request_id, reason)   - a cup that won't be made (reported as brew_failed)

    A failed cup ends the chain: the jug is wrong from there on, and whoever stands at the machine
    has to see to it (a blocking alert says what). Cups already made stay made - and booked."""

    def __init__(self, request_ids, product, options, source):
        self.ids = list(request_ids)
        self.product = product
        self.options = options
        self.source = source
        self.started = 1  # the first cup is started with the command itself
        self.done_at = None
        self.over = False

    def cup(self, request_id):
        """(k, n) of a request id of this chain, or None."""
        return (self.ids.index(request_id) + 1, len(self.ids)) if request_id in self.ids else None

    def step(self, events, busy, idle, ready_alert, blocking, now):
        actions = []
        for event in events:
            if event.get("request_id") not in self.ids:
                continue
            if event["type"] == "brew_failed":
                actions += self._fail_rest("previous cup failed: " + str(event.get("reason", "")))
            elif event["type"] == "brew_done":
                self.done_at = now
        if self.over or busy or self.done_at is None or self.started >= len(self.ids):
            if self.started >= len(self.ids) and not busy and self.done_at is not None:
                self.over = True
            return actions
        waited = now - self.done_at
        if blocking and waited >= READY_PAUSE:
            if waited >= READY_TIMEOUT:
                actions += self._fail_rest("blocked")
            return actions  # the machine asks for something first - maybe done in time
        if idle and waited >= READY_PAUSE and (ready_alert or waited >= READY_FALLBACK):
            actions.append(("start", self.ids[self.started]))
            self.started += 1
            self.done_at = None
        elif waited >= READY_TIMEOUT:
            actions += self._fail_rest("not ready")
        return actions

    def lost(self):
        return self._fail_rest("disconnected")

    def _fail_rest(self, reason):
        if self.over:
            return []
        self.over = True
        rest = self.ids[self.started:]
        self.started = len(self.ids)
        return [("fail", request_id, reason) for request_id in rest]
