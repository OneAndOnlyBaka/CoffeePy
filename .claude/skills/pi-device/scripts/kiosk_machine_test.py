"""The coffee machine's kiosk screens on the Pi's own Chromium, against the bridge's simulator
(MACHINE_SIMULATE=true): real touches, renderer crashes per step - kiosk_ui_test.py's method for
the brew. Runs ON the Pi next to cdp.py; kiosk-machine-test.sh sets it up and cleans up.

    python3 -I /tmp/kiosk_machine_test.py ROUNDS

Synthetic scans of the test card 11223344 ("Probe P.") - only with the user's OK for the run. A
round: Order now -> cup -> Start -> brewing -> thank you (one order); cup -> Cancel (nothing); a
free product from the wait screen (nothing booked); the machine blocked (Start disabled, red ring,
modal); the ring's modal open and closed; the runaways tab.
"""
import glob
import json
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import os  # noqa: E402

# KIOSK_BROWSER=wpe: the same run against WPE WebKit (cog) through wd.py - the browser trial.
if os.environ.get("KIOSK_BROWSER") == "wpe":
    import wd as cdp  # noqa: E402
else:
    import cdp  # noqa: E402

CARD = "11223344"
UID = 0x44332211
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def compose(*args, **kw):
    return subprocess.run(["sudo", "docker", "compose", *args], cwd="/opt/coffeepy", capture_output=True, text=True, **kw)


def sql(q):
    return compose("exec", "-T", "db", "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{q}"').stdout.strip()


def scan():
    sql(f"insert into coffee_nfcscan (uid, event, atr, card_type, created_at) values ('{CARD}', 'card_inserted', '', '', now())")


def simulate(*alerts):
    compose("exec", "-T", "mosquitto", "mosquitto_pub", "-t", "coffeemachine/cmd",
            "-m", json.dumps({"action": "simulate", "alerts": list(alerts)}))


def orders():
    return int(sql(f"select count(*) from coffee_order where member_id = {UID}") or 0)


def dispenses(state=None):
    where = f" where state = '{state}'" if state else ""
    return int(sql(f"select count(*) from coffee_machinedispense{where}") or 0)


def crashes():
    n = 0
    for f in glob.glob("/run/user/*/chromium.log"):
        r = subprocess.run(["sudo", "grep", "-c", "renderer crashed", f], capture_output=True, text=True)
        n += int(r.stdout.strip() or 0)
    return n


if hasattr(cdp, "crashes"):  # WPE: its web process's deaths in the journal, not chromium.log
    crashes = cdp.crashes  # noqa: F811


class Kiosk:
    def __init__(self):
        self.connect()

    def connect(self):
        for _ in range(60):
            try:
                self.page = cdp.Page()
                self.page.call("Page.enable")
                if self.ev("!!(window.htmx && document.getElementById('panel'))"):
                    self.count_swaps()
                    return
            except Exception:
                pass
            time.sleep(2)
        raise SystemExit("kiosk page not reachable")

    def count_swaps(self):
        """Counts #panel swaps - again after a renderer crash, whose reload starts a new page."""
        self.ev("window.swaps === undefined && (window.swaps = 0, document.body.addEventListener('htmx:afterSwap', e => { if (e.target.id === 'panel') window.swaps++; })); 1")

    def ev(self, js):
        try:
            return self.page.eval(js)
        except Exception:
            self.connect()
            return self.page.eval(js)

    def wait(self, js, timeout=12):
        end = time.time() + timeout
        while time.time() < end:
            if self.ev(f"!!({js})"):
                return True
            time.sleep(0.25)
        return False

    def tap(self, selector):
        box = self.ev(f"(e => e && (r => JSON.stringify([r.left + r.width / 2, r.top + r.height / 2]))(e.getBoundingClientRect()))(document.querySelector({json.dumps(selector)}))")
        if not box:
            return False
        x, y = json.loads(box)
        self.page.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        time.sleep(0.08)
        self.page.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
        time.sleep(0.25)
        return True

    def nav(self, mode):
        self.tap(f"a.nav-btn[data-mode={mode}]")
        return self.wait(f"(document.getElementById('panel-state') || {{dataset: {{}}}}).dataset.mode === '{mode}'")

    def swaps_while(self, seconds):
        """Panel swaps during `seconds` of standing on one screen - the polls must swap nothing."""
        self.count_swaps()
        s0 = self.ev("window.swaps")
        time.sleep(seconds)
        return self.ev("window.swaps") - s0


results = []
_last = [None]


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(time.strftime("%H:%M:%S ") + ("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def step(name):
    now = crashes()
    if _last[0] is not None and now != _last[0]:
        print(time.strftime("%H:%M:%S ") + f"CRASH before step '{name}' ({now - _last[0]})", flush=True)
    _last[0] = now
    print(time.strftime("%H:%M:%S ") + "step " + name, flush=True)


CUP = "document.querySelector('.brew-card.stage-cup')"
BREWING = "document.querySelector('.brew-card.stage-brewing')"
THANKS = "document.querySelector('.thank-you')"
WAIT = "document.getElementById('panel-state') && !document.querySelector('.brew-card') && !document.querySelector('input[name=coffee_id]') && !document.querySelector('.thank-you')"

k = Kiosk()
simulate("coffee_ready")

if len(sys.argv) > 2 and sys.argv[2] in ("plain", "cup"):
    # A/B of one step (SKILL.md): `plain` logs in and cancels on the order screen, `cup` goes on to
    # the cup screen and cancels there - the same swaps but one, the cup screen's.
    runs, variant, start = ROUNDS, sys.argv[2], crashes()
    for n in range(runs):
        k.nav("order")
        k.wait(WAIT, 8)
        scan()
        k.wait("document.querySelector('input[name=coffee_id]')")
        k.wait("false", 1)
        if variant == "cup":
            k.tap(".primary-action")
            k.wait(CUP, 5)
            k.wait("false", 1)
            k.tap(".brew-card .action-row button.danger")
        else:
            k.tap(".action-row button.danger")
        k.wait(WAIT, 5)
        k.wait("false", 1.5)
        now = crashes()
        print(time.strftime("%H:%M:%S ") + f"{variant} {n + 1}: crashes so far {now - start}", flush=True)
        if not k.ev("!!document.getElementById('panel')"):
            k.connect()
    print(json.dumps({"variant": variant, "runs": runs, "crashes": crashes() - start}))
    sys.exit(0)

start = crashes()
print("crashes before:", start, flush=True)

for r in range(1, ROUNDS + 1):
    print(f"--- round {r}", flush=True)
    c0 = crashes()

    step("order now -> cup")
    k.nav("order")
    k.wait(WAIT, 8)
    before, made = orders(), dispenses("done")
    scan()
    check("order: logged in", k.wait("document.querySelector('input[name=coffee_id]')"))
    k.wait("false", 1)
    k.tap(".primary-action")
    check("cup: shown", k.wait(CUP, 5))
    check("cup: nothing booked yet", orders() == before)
    check("cup: polls swap nothing", k.swaps_while(3) == 0)
    step("start -> brewing")
    k.tap(".brew-card .primary-action")
    check("brewing: shown", k.wait(BREWING, 8))
    k.wait("document.querySelector('img.brew-cup[src*=wait_order]')", 10)
    check("brewing: polls swap nothing while it pours", k.swaps_while(3) == 0)
    step("brewing -> thank you")
    check("thank you: shown", k.wait(THANKS, 30))
    check("thank you: one order booked", orders() == before + 1, f"{before} -> {orders()}")
    check("thank you: dispense done", dispenses("done") == made + 1)
    k.wait(WAIT, 30)

    step("cup -> cancel")
    before, asked = orders(), dispenses()
    scan()
    k.wait("document.querySelector('input[name=coffee_id]')")
    k.wait("false", 1)
    k.tap(".primary-action")
    k.wait(CUP, 5)
    k.tap(".brew-card .action-row button.danger")
    check("cancel: back to the wait screen", k.wait(WAIT, 5))
    check("cancel: nothing booked or asked", orders() == before and dispenses() == asked)

    step("free product")
    k.wait("false", 1)
    check("free: button on the wait screen", k.tap(".free-products button"))
    check("free: cup shown", k.wait(CUP, 5))
    k.tap(".brew-card .primary-action")
    brewing = k.wait(BREWING, 8)
    check("free: brewing", brewing, "" if brewing else "panel: " + str(k.ev(
        "(e => e ? e.className + ' | ' + e.innerText.replace(/\\s+/g, ' ').slice(0, 160) : document.getElementById('panel').innerText.replace(/\\s+/g, ' ').slice(0, 160))(document.querySelector('.brew-card'))"))
        + " | last dispense: " + sql("select id || ' ' || source || ' ' || state || ' ' || coalesce(reason, '') from coffee_machinedispense order by id desc limit 1")
        + " | machine: " + sql("select state->'progress'->>'phase' from coffee_machinestatus"))
    check("free: done, nothing booked", k.wait("document.querySelector('.brew-card.stage-done_free')", 30) and orders() == before)
    k.wait(WAIT, 30)

    step("blocked")
    simulate("fill_water")
    check("blocked: red ring", k.wait("document.querySelector('.machine-btn.machine-blocked')", 12))
    scan()
    k.wait("document.querySelector('input[name=coffee_id]')")
    k.wait("false", 1)
    k.tap(".primary-action")
    k.wait(CUP, 5)
    check("blocked: Start disabled", k.ev("!!document.querySelector('.brew-card .primary-action[disabled]')"))
    k.tap(".brew-card .action-row button.danger")
    k.wait(WAIT, 5)
    step("ring modal while blocked")  # the ring is on the wait screen only
    k.tap(".machine-btn")
    check("modal: opens with the machine", k.wait("document.getElementById('qr-modal').classList.contains('open') && document.querySelector('.machine-card')", 5))
    k.wait("false", 1.5)
    k.tap("#qr-modal .modal-close")
    check("modal: closes", k.wait("!document.getElementById('qr-modal').classList.contains('open')", 3))
    simulate("coffee_ready")
    check("ready again: green ring", k.wait("document.querySelector('.machine-btn.machine-ok, .machine-btn.machine-warn')", 12))

    step("modal open/close x3")
    for _ in range(3):
        k.tap(".machine-btn")
        k.wait("document.querySelector('.machine-card')", 5)
        k.wait("false", 1)
        k.tap("#qr-modal .modal-close")
        k.wait("false", 0.5)

    step("runaways tab")
    if k.ev("!document.querySelector('a.nav-btn[data-mode=ranking]').hidden"):
        k.nav("ranking")
        k.wait("false", 2)
        check("ranking: runaways tab there", k.tap(".ranking-tabs a[href*=runaways]"))
        check("ranking: runaways shown", k.wait("document.querySelector('.ranking-tabs a.active[href*=runaways]')", 5))
        check("ranking: polls swap nothing", k.swaps_while(3) == 0)
    step("end of round")
    k.nav("order")
    k.wait("false", 2)

    c1 = crashes()
    check(f"round {r}: no renderer crash", c1 == c0, f"{c1 - c0} crash(es)")
    if c1 != c0:
        k.connect()
        k.wait("false", 5)

simulate("coffee_ready")
print(json.dumps({"passed": sum(ok for _, ok in results), "failed": [n for n, ok in results if not ok],
                  "crashes": crashes() - start}))
