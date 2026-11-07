"""The app's side of the coffee machine bridge (coffee-machine/, its MQTT contract in README.md).

The bridge's retained topics land in MachineStatus via mqtt_listener (handle_message); the web
processes send commands and the config with one short MQTT publish each (send). Everything that
decides about a machine reads MachineStatus - never the bridge directly - so the kiosk keeps
working, from the tally alone, whatever the machine or the bridge do."""
import json
import logging
import os
import time
import uuid

from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy, pgettext_lazy

from coffee.models import (
    CoffeeSort, MachineCounter, MachineDispense, MachineIncident, MachineProduct, MachineStatus, MachineSupply, Member,
    Order,
)

log = logging.getLogger("coffee.machine")

PREFIX = os.environ.get("MACHINE_TOPIC", "coffeemachine")
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None

# The topics the bridge publishes and the listener stores; "cmd" and "config" are the app's own.
FROM_BRIDGE = ("status", "state", "counters", "products", "scan", "event")
SCAN_SECONDS = 40  # the bridge scans for 10 s, a busy machine connection can delay the answer


# The machine's messages as people read them - keys as py-jura names them (bits differ per model,
# the bridge sends the keys). Unknown keys show as they are; the ones only about the machine's mood
# ("coffee_ready", "energy_safe", ...) are not shown at all.
ALERT_LABELS = {
    "fill_water": gettext_lazy("Fill the water tank"),
    "empty_grounds": gettext_lazy("Empty the grounds container"),
    "empty_tray": gettext_lazy("Empty the drip tray"),
    "insert_tray": gettext_lazy("Put the drip tray in"),
    "insert_coffee_bin": gettext_lazy("Put the grounds container in"),
    "no_beans": gettext_lazy("Fill in coffee beans"),
    "fill_system": gettext_lazy("Fill the system"),
    "heating_up": gettext_lazy("Heating up"),
    "please_wait": gettext_lazy("Please wait"),
    "close_tab": gettext_lazy("Close the cover"),
    "remove_water_tank": gettext_lazy("Water tank removed"),
    "milk_alert": gettext_lazy("Milk system"),
    "error_status": gettext_lazy("Machine error"),
    "program_mode_status": gettext_lazy("In programming mode"),
    "switchoff_delay_active": gettext_lazy("Switching off"),
    "filter_alert": gettext_lazy("Change the filter"),
    "decalc_alert": gettext_lazy("Descale"),
    "cleaning_alert": gettext_lazy("Clean"),
    "cappu_rinse_alert": gettext_lazy("Rinse the milk system"),
    "cappu_clean_alert": gettext_lazy("Clean the milk system"),
    "lockedkeys": gettext_lazy("Keys locked"),
}
QUIET_ALERTS = {"coffee_ready", "energy_safe", "active_rf_filter", "welcome", "goodbye", "enjoy_product",
                "remotescreen", "system_filling", "coffee_rinsing", "steam_ready"}
MAINTENANCE_LABELS = {
    "cleaning": gettext_lazy("Cleaning"), "filter_change": pgettext_lazy("water filter", "Filter"),
    "decalc": gettext_lazy("Descaling"), "cappu_rinse": gettext_lazy("Milk system rinses"),
    "coffee_rinse": gettext_lazy("Coffee rinses"), "cappu_clean": gettext_lazy("Milk system cleanings"),
}


# The machine's product names (py-jura's, English) as the kiosk shows them - only where a product
# appears without a coffee sort of its own (free products, the dashboard's list).
PRODUCT_LABELS = {
    "Hot Water": gettext_lazy("Hot water"), "Milk Foam": gettext_lazy("Milk foam"),
    "Coffee": gettext_lazy("Coffee"), "2 Coffee": gettext_lazy("2 coffees"), "2 Coffees": gettext_lazy("2 coffees"),
    "Milk Coffee": gettext_lazy("Milk coffee"), "Hot Water Green Tea": gettext_lazy("Hot water for green tea"),
}


def product_label(name: str) -> str:
    return str(PRODUCT_LABELS.get(name, name))


def kiosk_level() -> str | None:
    """The kiosk's machine sign: None without a machine, "off" not connected, "blocked" when it
    can't make anything, "warn" when something wants doing soon, "ok" otherwise."""
    from dashboard.models import CoffeeMachine  # noqa: PLC0415

    if not CoffeeMachine.current().configured:
        return None
    current = status()
    if not current["connected"]:
        return "off"
    if current["blocking"]:
        return "blocked"
    return "warn" if attention(current) else "ok"


WARN_ALERTS = {"no_beans", "filter_alert", "decalc_alert", "cleaning_alert", "cappu_rinse_alert",
               "cappu_clean_alert", "milk_alert", "info_decalc_alert", "info_filter_used_up_alert"}
DUE_PERCENT = 90  # maintenance this far along shows as "soon"


def attention(current: dict) -> list[str]:
    """What wants doing soon, as people read it - messages that don't block yet, and maintenance
    nearly due."""
    items = [alert_label(k) for k in current["alerts"] if k in WARN_ALERTS]
    percent = (current["counters"].get("maintenance") or {}).get("percent") or {}
    items += [str(MAINTENANCE_LABELS.get(k, k)) for k, v in percent.items() if v >= DUE_PERCENT]
    return items


def alert_label(key: str) -> str:
    return str(ALERT_LABELS.get(key, key))


def shown_alerts(keys) -> list[str]:
    return [k for k in keys or () if k not in QUIET_ALERTS and not k.startswith("bit_")]


def topic(name: str) -> str:
    return f"{PREFIX}/{name}"


def send(name: str, payload, retain=False) -> bool:
    """One publish to the broker - from a web request, so it must not hang: a broker that isn't
    there costs a short timeout, and the caller says the machine didn't get it."""
    from paho.mqtt import publish  # noqa: PLC0415 - only where something is sent

    auth = {"username": MQTT_USER, "password": MQTT_PASSWORD} if MQTT_USER else None
    try:
        publish.single(topic(name), json.dumps(payload), qos=1, retain=retain, hostname=MQTT_HOST,
                       port=MQTT_PORT, auth=auth, keepalive=5, client_id=f"coffeepy-web-{uuid.uuid4().hex[:8]}")
    except (OSError, ValueError) as exc:
        log.warning("could not publish %s: %s", name, exc)
        return False
    return True


# --- the config ---------------------------------------------------------------------------------

def config_payload() -> dict:
    from dashboard.models import CoffeeMachine  # noqa: PLC0415 - dashboard imports coffee

    machine = CoffeeMachine.current()
    return {"driver": machine.driver, "address": machine.address if machine.enabled else "",
            "block_manual": machine.block_manual}


def send_config() -> bool:
    return send("config", config_payload(), retain=True)


# --- commands -----------------------------------------------------------------------------------

def request_scan() -> str:
    """Asks the bridge to look for machines; the answer appears in scan_result() under this id."""
    request_id = uuid.uuid4().hex
    with transaction.atomic():
        row = MachineStatus.current()
        row.scan = {"id": request_id, "requested": time.time(), "devices": [], "error": "", "done": False}
        row.save()
    if not send("cmd", {"id": request_id, "action": "scan"}):
        store({"scan": {**row.scan, "done": True, "error": "unreachable"}})
    return request_id


def scan_result(request_id: str) -> dict:
    """{"done", "devices", "error"} for the dashboard's poll. "timeout" once the bridge had long
    enough - no bridge (an older device's stack), or one that hangs."""
    scan = MachineStatus.current().scan or {}
    if scan.get("id") != request_id:
        return {"done": True, "devices": [], "error": "superseded"}
    if not scan.get("done") and time.time() - scan.get("requested", 0) > SCAN_SECONDS:
        return {**scan, "done": True, "error": "timeout"}
    return scan


# --- what the bridge sends ----------------------------------------------------------------------

def store(fields: dict):
    MachineStatus.objects.update_or_create(pk=1, defaults=fields)


def parse(name: str, raw: bytes):
    """The payload of one of the bridge's topics, or None if it isn't one it would send."""
    if name not in FROM_BRIDGE:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    if name == "status":
        return text if text in ("online", "offline", "disabled") else None
    try:
        payload = json.loads(text or "{}")
    except ValueError:
        log.warning("ignoring non-JSON %s: %r", name, raw[:200])
        return None
    return payload if isinstance(payload, dict) else None


def row_fields(name: str, payload) -> dict | None:
    """What a retained topic changes in MachineStatus - None for the ones that aren't state."""
    if name == "status":
        return {"status": payload, "online": payload in ("online", "disabled")}
    if name in ("state", "counters", "products"):
        return {name: payload}
    return None


def handle_scan(payload: dict):
    """The answer to request_scan - only for the request still waited for."""
    with transaction.atomic():
        row = MachineStatus.objects.select_for_update().filter(pk=1).first()
        if row is None or (row.scan or {}).get("id") != payload.get("id"):
            return
        row.scan = {**row.scan, "devices": payload.get("devices") or [], "error": payload.get("error") or "",
                    "done": True}
        row.save(update_fields=["scan", "updated_at"])


# --- products and coffee sorts ------------------------------------------------------------------

# What a JURA calls its products (py-jura's names) against what a German-speaking group calls the
# sort it sells as - the automatic match tries both; anything else is matched by hand on the page.
NAME_ALIASES = {
    "coffee": ("kaffee", "cafe crema", "caffe crema", "crema"),
    "2 coffee": ("2 kaffee", "zwei kaffee", "doppelter kaffee"),
    "2 coffees": ("2 kaffee", "zwei kaffee", "doppelter kaffee"),
    "2 espressi": ("2 espresso", "zwei espresso"),
    "hot water": ("heisses wasser", "heißes wasser", "wasser", "tee"),
    "milk foam": ("milchschaum",),
    "milk coffee": ("milchkaffee",),
    "espresso doppio": ("doppio", "doppelter espresso"),
    "cafe barista": ("caffe barista", "barista"),
    "barista lungo": ("lungo", "lungo barista"),
}


def _norm(name: str) -> str:
    return "".join(c for c in name.lower().replace("ß", "ss") if c.isalnum())


def _names(product_name: str) -> set[str]:
    key = product_name.lower()
    return {_norm(product_name), *(_norm(a) for a in NAME_ALIASES.get(key, ()))}


def sync_products(payload: dict) -> dict:
    """The machine's product list into MachineProduct, matched to the coffee sorts: a product
    without a sort gets the one of the same name (or a known German one), and every sort no present
    product is linked to is obsolete - not offered at the kiosk any more - unless it is marked as one
    without the machine. Returns what changed, for the audit log. Only with a machine configured."""
    from dashboard.models import CoffeeMachine  # noqa: PLC0415

    if not CoffeeMachine.current().configured:
        return {}
    listed = {int(p["code"]): p for p in payload.get("products") or [] if "code" in p}
    if not listed:
        return {}  # an empty list is a machine that didn't say, not one that makes nothing
    changes = {"linked": [], "obsolete": [], "offered": []}
    with transaction.atomic():
        for code, item in listed.items():
            product, created = MachineProduct.objects.get_or_create(code=code, defaults={
                "name": item.get("name") or f"#{code}", "strokes": item.get("strokes") or 1,
                "free": code in FREE_BY_DEFAULT})
            product.name = item.get("name") or product.name
            product.present = True
            product.brewable = bool(item.get("brewable", True))
            # The machine's own amount where its definition has one; otherwise the device page's - the
            # E6 says nothing about hot water, and every reconnect used to clear what the admin entered.
            if product.brewable or item.get("water_ml"):
                product.water_ml = item.get("water_ml")
            product.recipe = item.get("recipe") or {}
            product.save()
        MachineProduct.objects.exclude(code__in=listed).update(present=False)
        sorts = {_norm(s.name): s for s in CoffeeSort.objects.filter(machine_product__isnull=True)}
        for product in MachineProduct.objects.filter(present=True, coffee_sort__isnull=True, free=False):
            match = next((sorts.pop(n) for n in _names(product.name) if n in sorts), None)
            if match is not None:
                product.coffee_sort = match
                product.save(update_fields=["coffee_sort"])
                changes["linked"].append(f"{product.name} = {match.name}")
        linked = set(MachineProduct.objects.filter(present=True, coffee_sort__isnull=False)
                     .values_list("coffee_sort_id", flat=True))
        for sort in CoffeeSort.objects.all():
            obsolete = sort.pk not in linked and not sort.without_machine
            if obsolete != sort.is_obsolete:
                sort.is_obsolete = obsolete
                sort.save(update_fields=["is_obsolete"])
                changes["obsolete" if obsolete else "offered"].append(sort.name)
    return {k: v for k, v in changes.items() if v}


def unsync_products():
    """The machine switched off: every sort is offered again, as before there was one."""
    names = list(CoffeeSort.objects.filter(is_obsolete=True).values_list("name", flat=True))
    CoffeeSort.objects.filter(is_obsolete=True).update(is_obsolete=False)
    return names


# Products that are free by default when the machine first lists them: water and milk foam are
# what a group usually doesn't put on the tally. A choice on the page.
FREE_BY_DEFAULT = {0x0D, 0x08, 0x2D, 0x3D}


def suggested_price(strokes: int) -> Decimal:
    """For a product adopted as a new sort: what the sorts sold cost per stroke on average."""
    average = CoffeeSort.offered().filter(machine_strokes__gt=0).aggregate(
        per=Avg(F("price") / F("machine_strokes")))["per"]
    if not average:
        return Decimal("0.00")
    step = Decimal("0.05")
    return (Decimal(str(average)) * strokes / step).to_integral_value(rounding="ROUND_HALF_UP") * step


def adopt_product(product: MachineProduct) -> CoffeeSort:
    """A product of the machine as a new coffee sort, linked to it."""
    name, n = product.name, 2
    while CoffeeSort.objects.filter(name=name).exists():
        name, n = f"{product.name} ({n})", n + 1
    sort = CoffeeSort.objects.create(name=name, price=suggested_price(product.strokes),
                                     machine_strokes=product.strokes)
    product.coffee_sort = sort
    product.save(update_fields=["coffee_sort"])
    return sort


# --- brews ----------------------------------------------------------------------------------------

# A brew the machine started but whose end never came through (the connection or the bridge went)
# is booked after this: the coffee was most likely made. One asked for and never started fails.
STARTED_TIMEOUT = 300
REQUESTED_TIMEOUT = 90
CHAIN_TIMEOUT = 180  # the bridge's READY_TIMEOUT and a margin


# Free products the kiosk's wait screen offers: what fits in one row under its text at 1024x600 (a
# fifth button is ~150px wide) - never a second row or a scrolling one (CLAUDE.md). The device page
# says so.
FREE_ON_KIOSK = 5


def can_brew(product: MachineProduct) -> bool:
    """Whether a brew of it can be asked for: the bridge knows its recipe from the machine's
    definition, or the page names its water (the E6's hot water isn't in py-jura's definition)."""
    return product.present and (product.brewable or bool(product.water_ml))


def product_for(sort: CoffeeSort | None) -> MachineProduct | None:
    """The product a sort is brewed as at the machine - only while that machine can make it."""
    if sort is None:
        return None
    product = MachineProduct.objects.filter(coffee_sort=sort, present=True).first()
    return product if product is not None and can_brew(product) else None


def brewing_available() -> bool:
    """Whether the kiosk guides through the brew now: a machine set up and connected."""
    return status()["connected"]


def request_brew(product: MachineProduct, member: Member | None, sort: CoffeeSort | None,
                 source=MachineDispense.SOURCE_KIOSK, options: dict | None = None, count: int = 1) -> MachineDispense:
    """Asks the bridge for one product - or `count` cups of it in a row, into one big cup or a jug (a
    member's slot, coffee/slots.py). One dispense row per cup follows what the machine does
    (handle_event); a kiosk brew is booked cup by cup once each is done - never before. Returns the
    first cup's row."""
    options = dict(options or {})
    if not product.brewable and product.water_ml and "water_ml" not in options:
        options["water_ml"] = product.water_ml
    cups = []
    for cup in range(1, max(1, count) + 1):
        request_id = uuid.uuid4().hex
        cups.append(MachineDispense.objects.create(
            request_id=request_id, code=product.code, product_name=product.name, source=source, member=member,
            coffee_sort=sort, chain=cups[0].request_id if cups else (request_id if count > 1 else ""),
            cup=cup, of=max(1, count), requested=options))
    command = {"id": cups[0].request_id, "action": "brew", "product": product.code, "source": source}
    if options:
        command["options"] = options
    if len(cups) > 1:
        command["request_ids"] = [d.request_id for d in cups]
    if not send("cmd", command):
        MachineDispense.objects.filter(pk__in=[d.pk for d in cups]).update(
            state=MachineDispense.FAILED, reason="unreachable", finished_at=timezone.now())
        cups[0].refresh_from_db()
    return cups[0]


def chain_progress(first: MachineDispense) -> dict:
    """Where a brew of several cups stands: {"cup": the one running or next, "of", "done", "failed",
    "over"} - for the kiosk's "cup k of n"."""
    cups = list(MachineDispense.objects.filter(chain=first.chain).order_by("cup")) if first.chain else [first]
    done = sum(1 for d in cups if d.state == MachineDispense.DONE)
    failed = [d for d in cups if d.state == MachineDispense.FAILED]
    running = next((d for d in cups if d.state in (MachineDispense.REQUESTED, MachineDispense.STARTED)), None)
    return {"cup": running.cup if running else len(cups), "of": len(cups), "done": done,
            "failed": failed[0] if failed else None, "over": running is None, "current": running}


def handle_event(payload: dict):
    """The bridge's brew events into MachineDispense - and a kiosk brew that is done into its order."""
    kind, request_id = payload.get("type"), payload.get("request_id") or ""
    code = int(payload.get("product") or 0)
    with transaction.atomic():
        dispense = MachineDispense.objects.select_for_update().filter(request_id=request_id).first() if request_id else None
        if dispense is None and kind == "brew_started":
            product = MachineProduct.objects.filter(code=code).first()
            dispense = MachineDispense.objects.create(
                request_id=request_id, code=code, product_name=product.name if product else f"#{code}",
                source=payload.get("source") or MachineDispense.SOURCE_MANUAL, state=MachineDispense.STARTED,
                coffee_sort=product.coffee_sort if product else None)
        elif dispense is None and kind == "brew_done":
            # A brew at the machine: the one that started last and isn't done.
            dispense = MachineDispense.objects.select_for_update().filter(
                request_id="", code=code, state=MachineDispense.STARTED).first()
        if dispense is None:
            log.info("machine event without a brew to it: %s", payload)
            return None
        was_done = dispense.state == MachineDispense.DONE
        for key in ("water_ml", "strength"):
            if payload.get(key) is not None:
                setattr(dispense, key, payload[key])
        if kind == "brew_started":
            dispense.state = MachineDispense.STARTED
        elif kind == "brew_done":
            dispense.state, dispense.finished_at = MachineDispense.DONE, timezone.now()
        elif kind == "brew_failed":
            dispense.state, dispense.finished_at = MachineDispense.FAILED, timezone.now()
            dispense.reason = str(payload.get("reason") or "")[:200]
        dispense.save()
        if dispense.state == MachineDispense.DONE and not was_done:  # an event can come twice
            book(dispense)
            count_supplies(dispense)
    return dispense


def book(dispense: MachineDispense, reason: str = "") -> Order | None:
    """The order for a kiosk brew that was made - once, whoever gets here first (the event, or
    the timeout for one whose end never came)."""
    from dashboard import audit  # noqa: PLC0415 - dashboard imports coffee

    if dispense.source != MachineDispense.SOURCE_KIOSK or dispense.order_id or dispense.member_id is None:
        return None
    sort = dispense.coffee_sort
    if sort is None:
        return None
    made = ", ".join([dispense.product_name] + [f"{k.replace('_ml', '')} {v}{' ml' if k == 'water_ml' else ''}"
                                                 for k, v in (dispense.requested or {}).items()]
                     + ([f"cup {dispense.cup}/{dispense.of}"] if dispense.of > 1 else []))
    order = Order.objects.create(member_id=dispense.member_id, coffee_sort=sort, value=sort.price,
                                 machine_strokes=sort.machine_strokes,
                                 info=f"Ordered [{sort.pk}] {sort.name} (machine: {made})"[:255])
    dispense.order = order
    dispense.save(update_fields=["order"])
    who = audit.member_actor(dispense.member)
    audit.log_actor(who, "order.create", target=who, source="kiosk",
                    details=f"#{order.pk}: {sort.name}, {order.value} EUR, {order.machine_strokes} cup(s), "
                            f"made by the machine{', ' + reason if reason else ''}")
    return order


def housekeeping(now=None):
    """Brews whose end never arrived: booked (started) or failed (never started). The listener runs
    it every half minute."""
    now = now or timezone.now()
    for dispense in MachineDispense.objects.filter(
            state=MachineDispense.STARTED, created_at__lt=now - timezone.timedelta(seconds=STARTED_TIMEOUT)):
        with transaction.atomic():
            dispense.state, dispense.finished_at, dispense.reason = MachineDispense.DONE, now, "no confirmation"
            dispense.save()
            book(dispense, reason="no confirmation from the machine")
            count_supplies(dispense)
    MachineDispense.objects.filter(
        state=MachineDispense.REQUESTED, cup=1, created_at__lt=now - timezone.timedelta(seconds=REQUESTED_TIMEOUT)
    ).update(state=MachineDispense.FAILED, finished_at=now, reason="no answer")
    # A later cup of several waits for the one before - its time runs from that one's end (the bridge
    # needs up to its READY_TIMEOUT to start it, 2 minutes).
    for later in MachineDispense.objects.filter(state=MachineDispense.REQUESTED, cup__gt=1):
        before = MachineDispense.objects.filter(chain=later.chain, cup=later.cup - 1).first()
        if before is None or (before.state in (MachineDispense.DONE, MachineDispense.FAILED) and before.finished_at
                              and before.finished_at < now - timezone.timedelta(seconds=CHAIN_TIMEOUT)):
            later.state, later.finished_at, later.reason = MachineDispense.FAILED, now, "no answer"
            later.save(update_fields=["state", "finished_at", "reason"])
    incidents_housekeeping(now)


# --- counters ------------------------------------------------------------------------------------

def record_counters(payload: dict) -> MachineCounter | None:
    """A reading of the machine's counters - kept when something counted since the last one."""
    products = {str(k): int(v) for k, v in (payload.get("products") or {}).items() if v}
    if not products:
        return None
    last = MachineCounter.objects.first()
    if last is not None and last.products == products:
        return None
    return MachineCounter.objects.create(total=int(payload.get("total") or 0), products=products,
                                         maintenance=payload.get("maintenance") or {})


# --- for the pages ------------------------------------------------------------------------------

def status() -> dict:
    """Everything the dashboard shows about the machine, in one dict."""
    from dashboard.models import CoffeeMachine  # noqa: PLC0415

    machine, row = CoffeeMachine.current(), MachineStatus.current()
    state = row.state or {}
    return {
        "machine": machine,
        "row": row,
        "bridge": row.status or "",  # "" = the bridge never reported (no container, or not yet)
        "connected": row.connected and machine.configured and state.get("address", "").upper() == machine.address.upper(),
        "state": state,
        "alerts": shown_alerts(state.get("alerts")),
        "blocking": state.get("blocking") or [],
        "counters": row.counters or {},
        "products": (row.products or {}).get("products") or [],
    }


def counter_rows(status_: dict) -> list[dict]:
    """[{"name", "count"}] per product the machine counted, most first - the names from the bridge's
    product list, a code it doesn't name as it is."""
    names = {str(p["code"]): p["name"] for p in status_["products"]}
    counted = (status_["counters"].get("products") or {}).items()
    rows = [{"code": code, "name": names.get(str(code), f"#{code}"), "count": n} for code, n in counted if n]
    return sorted(rows, key=lambda r: -r["count"])



# --- supplies: water, grounds, beans -------------------------------------------------------------

# Defaults for a new device - a JURA E6's: a 1.9 l tank, a container for about 16 portions, 280 g
# of beans and ~8 g a portion. The page lets them be changed for another machine.
SUPPLY_DEFAULTS = {
    MachineSupply.WATER: {"capacity": 1900},
    MachineSupply.GROUNDS: {"capacity": 16},
    MachineSupply.BEANS: {"capacity": 280, "per_portion": 8},
}
# The machine's messages that say a supply was refilled (or emptied) when they go away.
REFILLED_BY = {
    MachineSupply.WATER: ("fill_water", "remove_water_tank"),
    MachineSupply.GROUNDS: ("empty_grounds", "insert_coffee_bin"),
    MachineSupply.BEANS: ("no_beans",),
}
SUPPLY_LABELS = {MachineSupply.WATER: gettext_lazy("Water"), MachineSupply.GROUNDS: gettext_lazy("Grounds container"),
                 MachineSupply.BEANS: gettext_lazy("Beans")}
SOON_PERCENT = 80  # this much used: the kiosk asks the one at the machine to see to it


def supplies() -> dict:
    """{kind: MachineSupply}, created with the defaults the first time."""
    found = {s.kind: s for s in MachineSupply.objects.all()}
    for kind, defaults in SUPPLY_DEFAULTS.items():
        if kind not in found:
            found[kind] = MachineSupply.objects.create(kind=kind, **defaults)
    return found


def _takes(dispense: MachineDispense) -> dict:
    """What one product took: its water (the machine's own recipe of this brew), and - a coffee,
    not water or milk - its portions of grounds and beans."""
    product = MachineProduct.objects.filter(code=dispense.code).first()
    water = dispense.water_ml or (product.water_ml if product else 0) or 0
    coffee = dispense.strength is not None or (product is not None and product.code not in FREE_BY_DEFAULT)
    portions = (product.strokes if product else 1) if coffee else 0
    return {MachineSupply.WATER: water, MachineSupply.GROUNDS: portions, MachineSupply.BEANS: portions}


def count_supplies(dispense: MachineDispense):
    found = supplies()
    for kind, amount in _takes(dispense).items():
        if amount:
            supply = found[kind]
            supply.used += amount * (supply.per_portion if kind == MachineSupply.BEANS else 1)
            supply.save(update_fields=["used"])


def refill(kind: str, actor: str = "") -> MachineSupply:
    from dashboard import audit  # noqa: PLC0415

    supply = supplies()[kind]
    supply.used, supply.reset_at = 0, timezone.now()
    supply.save(update_fields=["used", "reset_at"])
    audit.log_actor(actor or "coffee machine", "machine.refill", target=kind,
                    source="kiosk" if actor else "system")
    return supply


def supply_levels() -> dict:
    """For the kiosk's machine popup."""
    if not MachineProduct.objects.exists():
        return {"supplies": []}
    return {"supplies": [{"kind": s.kind, "label": str(SUPPLY_LABELS[s.kind]), "used": s.used_percent,
                          "left": s.left_percent} for s in supplies().values()]}


def tasks_before(product: MachineProduct, water_ml: int | None = None, count: int = 1) -> list[str]:
    """What the one at the cup screen should do before Start: water the brew wouldn't get (all of its
    cups), beans nearly gone. Both are safe to do before - the machine isn't running yet."""
    found = supplies()
    water = found[MachineSupply.WATER]
    tasks = []
    need = (water_ml or product.water_ml or 0) * max(1, count)
    if need and water.capacity - water.used < need:
        tasks.append(str(ALERT_LABELS["fill_water"]))
    if found[MachineSupply.BEANS].used_percent >= SOON_PERCENT and product.code not in FREE_BY_DEFAULT:
        tasks.append(str(ALERT_LABELS["no_beans"]))
    return tasks


def tasks_after(product: MachineProduct, water_ml: int | None = None, count: int = 1) -> list[str]:
    """What the one waiting for the brew should do right after it - not during it: pulling the tank
    or the grounds container stops the machine."""
    found = supplies()
    tasks = []
    grounds = found[MachineSupply.GROUNDS]
    portions = (product.strokes if product.code not in FREE_BY_DEFAULT else 0) * max(1, count)
    if portions and (grounds.used + portions) * 100 / max(1, grounds.capacity) >= SOON_PERCENT:
        tasks.append(str(ALERT_LABELS["empty_grounds"]))
    water = found[MachineSupply.WATER]
    if (water.used + (water_ml or product.water_ml or 0) * max(1, count)) * 100 / max(1, water.capacity) >= SOON_PERCENT:
        tasks.append(str(ALERT_LABELS["fill_water"]))
    return tasks


# --- incidents: who left it for the next one ------------------------------------------------------

INCIDENT_ALERTS = ("fill_water", "empty_grounds", "empty_tray", "no_beans")
# A message this soon after a brew (or during it) is that brew's member's to see to.
INCIDENT_WINDOW = 60
# Their time to do it: the thank-you waits for it, and after this they "ran off".
INCIDENT_GRACE = 180


def track_alerts(previous: list, current: list, now=None):
    """The machine's messages changed: refill what was refilled, open an incident for whoever brewed
    just now, close the ones that are done. Called by the listener with each new state."""
    from dashboard import audit  # noqa: PLC0415

    now = now or timezone.now()
    previous, current = set(previous or ()), set(current or ())
    for kind, keys in REFILLED_BY.items():
        if any(k in previous and k not in current for k in keys):
            refill(kind)
    for key in INCIDENT_ALERTS:
        if key in current and key not in previous:
            window = now - timezone.timedelta(seconds=INCIDENT_WINDOW)
            brew = MachineDispense.objects.filter(
                source=MachineDispense.SOURCE_KIOSK, member__isnull=False,
                created_at__gte=now - timezone.timedelta(seconds=STARTED_TIMEOUT)).filter(
                Q(state=MachineDispense.STARTED) | Q(state=MachineDispense.DONE, finished_at__gte=window)).first()
            if brew is not None and not MachineIncident.objects.filter(alert=key, cleared_at__isnull=True).exists():
                MachineIncident.objects.create(member=brew.member, alert=key, dispense=brew, raised_at=now)
        elif key in previous and key not in current:
            for incident in MachineIncident.objects.filter(alert=key, cleared_at__isnull=True):
                incident.cleared_at = now
                if incident.outcome == MachineIncident.OPEN:
                    incident.outcome, incident.hero = MachineIncident.FIXED, None
                elif incident.hero_id == incident.member_id:
                    incident.hero = None  # came back and did it after all - still too late
                incident.save()
                if incident.hero_id:
                    audit.log_actor("coffee machine", "machine_incident.hero", target=audit.member_actor(incident.hero),
                                    details=key)


def incidents_housekeeping(now=None):
    """Open incidents past their grace: ran off."""
    from dashboard import audit  # noqa: PLC0415

    now = now or timezone.now()
    for incident in MachineIncident.objects.filter(outcome=MachineIncident.OPEN, cleared_at__isnull=True,
                                                   raised_at__lt=now - timezone.timedelta(seconds=INCIDENT_GRACE)):
        incident.outcome = MachineIncident.LEFT
        incident.save(update_fields=["outcome"])
        audit.log_actor("coffee machine", "machine_incident.left", target=audit.member_actor(incident.member),
                        details=incident.alert)


def note_visitor(member: Member):
    """A member logged in at the kiosk: for every incident someone ran off from, the likeliest one to
    put it right is the last one there before it was done."""
    MachineIncident.objects.filter(outcome=MachineIncident.LEFT, cleared_at__isnull=True).update(hero=member)


def open_tasks(member: Member | None) -> list[dict]:
    """What the machine is waiting for from this member, for their thank-you screen."""
    if member is None:
        return []
    return [{"alert": i.alert, "label": alert_label(i.alert)}
            for i in MachineIncident.objects.filter(member=member, outcome=MachineIncident.OPEN, cleared_at__isnull=True)]


def runaway_board(since=None) -> list[dict]:
    """The kiosk's "Ran off" board: per member, how often they left something, and how often they
    stepped in for someone - heroes first among equals."""
    from collections import Counter  # noqa: PLC0415

    incidents = MachineIncident.objects.select_related("member", "hero")
    if since is not None:
        incidents = incidents.filter(raised_at__gte=since)
    left, heroic, names = Counter(), Counter(), {}
    for incident in incidents:
        if incident.outcome == MachineIncident.LEFT and incident.member is not None:
            left[incident.member_id] += 1
            names[incident.member_id] = incident.member.display_name()
        if incident.hero is not None and incident.cleared_at is not None:
            heroic[incident.hero_id] += 1
            names[incident.hero_id] = incident.hero.display_name()
    rows = [{"uid": uid, "name": names[uid], "left": left[uid], "heroic": heroic[uid]} for uid in names]
    return sorted(rows, key=lambda r: (-r["left"], r["heroic"], r["name"]))
