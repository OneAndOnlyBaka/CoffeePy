"""Intensive kiosk UI test on the Pi's own Chromium: real touch events over DevTools, every screen
for several rounds, renderer crashes counted per step from the kiosk session's log. Runs ON the Pi
(stdlib only, next to cdp.py) - kiosk-ui-test.sh copies both and sets it up.

    python3 -I /tmp/kiosk_ui_test.py ROUNDS

It logs in with synthetic scans of a test card (11223344, member "Probe P."): only with the
user's OK for the run (SKILL.md) - kiosk-ui-test.sh creates the member, a second payment method
and switches the boards on, and removes them afterwards. Needs the coffee sorts Kaffee, Espresso
and Kanne (kiosk-ui-test.sh adds the missing ones). A round: Settings (a favourite tile, the
keyboard overlay with Shift, Save), Order (pick a sort - no swap, logout countdown - Cancel; then
Order now), Pay (numeric keyboard, method tile, Pay), Ranking. Each crash is reported with the
step before it ("CRASH before step ...").
"""
import glob
import json
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import cdp  # noqa: E402

CARD = "11223344"          # uid_as_int little-endian: 0x44332211
UID = 0x44332211
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def sql(q):
    out = subprocess.run(["sudo", "docker", "compose", "exec", "-T", "db", "sh", "-c",
                          f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{q}"'],
                         cwd="/opt/coffeepy", capture_output=True, text=True)
    return out.stdout.strip()


def scan():
    sql(f"insert into coffee_nfcscan (uid, event, atr, card_type, created_at) values ('{CARD}', 'card_inserted', '', '', now())")


def crashes():
    n = 0
    for f in glob.glob("/run/user/*/chromium.log"):
        r = subprocess.run(["sudo", "grep", "-c", "renderer crashed", f], capture_output=True, text=True)
        n += int(r.stdout.strip() or 0)
    return n


class Kiosk:
    def __init__(self):
        self.connect()

    def connect(self):
        for _ in range(60):
            try:
                self.page = cdp.Page()
                self.page.call("Page.enable")
                if self.ev("!!(window.htmx && document.getElementById('panel'))"):
                    return
            except Exception:
                pass
            time.sleep(2)
        raise SystemExit("kiosk page not reachable")

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

    def tap(self, selector, text=None):
        """A real touch on the element's centre - what a finger does, pointer events included."""
        find = (f"[...document.querySelectorAll({json.dumps(selector)})].find(e => e.textContent.trim() === {json.dumps(text)})"
                if text is not None else f"document.querySelector({json.dumps(selector)})")
        box = self.ev(f"(e => e && (r => JSON.stringify([r.left + r.width / 2, r.top + r.height / 2]))(e.getBoundingClientRect()))({find})")
        if not box:
            return False
        x, y = json.loads(box)
        self.page.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        time.sleep(0.08)
        self.page.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
        time.sleep(0.25)
        return True

    def swipe_up(self, selector, distance=250):  # kept for comparisons: a list that scrolls
        """A finger dragged up over the element - a real touch scroll, the compositor's job."""
        box = self.ev(f"(r => JSON.stringify([r.left + r.width / 2, r.top + r.height * 0.8]))(document.querySelector({json.dumps(selector)}).getBoundingClientRect())")
        x, y = json.loads(box)
        self.page.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        for step in range(1, 11):
            time.sleep(0.02)
            self.page.call("Input.dispatchTouchEvent", type="touchMove", touchPoints=[{"x": x, "y": y - distance * step / 10}])
        self.page.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
        time.sleep(0.6)

    def visible_unpicked(self, container, name):
        """The text of the first tile in sight that isn't the current pick - what a finger would
        reach without scrolling, and a tap that changes something."""
        return self.ev(f"""(() => {{ const body = document.querySelector('.panel-body').getBoundingClientRect();
          const tile = [...document.querySelectorAll('{container} .coffee-option')].find(l => {{
            const r = l.getBoundingClientRect(); const input = l.querySelector('input[name={name}]');
            return input && !input.checked && r.top >= body.top && r.bottom <= body.bottom; }});
          return tile ? tile.querySelector('span').childNodes.length > 1 ? tile.querySelector('.sort-text span').textContent : tile.querySelector('span').textContent : null; }})()""")

    def mode(self):
        return self.ev("(document.getElementById('panel-state') || {dataset: {}}).dataset.mode")

    def nav(self, mode):
        self.tap(f"a.nav-btn[data-mode={mode}]")
        return self.wait(f"(document.getElementById('panel-state') || {{dataset: {{}}}}).dataset.mode === '{mode}'")

    def visible(self, selector):
        return self.ev(f"(e => !!e && getComputedStyle(e).visibility === 'visible')(document.querySelector({json.dumps(selector)}))")


results = []


_last_crashes = [None]


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(time.strftime("%H:%M:%S ") + ("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def step(name):
    """Logs the step and whether the renderer crashed since the last one."""
    now = crashes()
    if _last_crashes[0] is not None and now != _last_crashes[0]:
        print(time.strftime("%H:%M:%S ") + f"CRASH before step '{name}' ({now - _last_crashes[0]})", flush=True)
    _last_crashes[0] = now
    print(time.strftime("%H:%M:%S ") + "step " + name, flush=True)


def type_on_osk(k, keys, numeric=False):
    for key in keys:
        if not k.tap("#osk-numeric .osk-key" if numeric else "#osk-text .osk-key", key):
            return False
    return True


k = Kiosk()
k.ev("window.swaps = 0; document.body.addEventListener('htmx:afterSwap', e => { if (e.target.id === 'panel') window.swaps++; }); window.samePage = 1; 1")
start_crashes = crashes()
print("crashes before:", start_crashes, flush=True)

for r in range(1, ROUNDS + 1):
    print(f"--- round {r}", flush=True)
    c0 = crashes()

    step("settings")
    # Settings: tiles, keyboard overlay with Shift, save
    k.nav("settings")
    scan()
    check("settings: logged in", k.wait("document.querySelector('input[name=favourite_coffee_id]')"))
    k.wait("false", 1)
    s0 = k.ev("window.swaps")
    target = k.visible_unpicked(".coffee-grid", "favourite_coffee_id")
    k.tap(".coffee-grid .coffee-option span", target)
    k.wait("false", 1.5)
    picked = k.ev("(document.querySelector('input[name=favourite_coffee_id]:checked') || {parentElement: {innerText: ''}}).parentElement.innerText.trim()")
    check("settings: tile picks", picked == target, picked)
    check("settings: no swap after the pick", k.ev("window.swaps") == s0, f"{s0} -> {k.ev('window.swaps')}")
    k.tap(".osk-open-btn")
    check("osk: opens", k.wait("getComputedStyle(document.getElementById('osk-modal')).visibility === 'visible'", 5))
    k.ev("document.getElementById('osk-input').value = ''; 1")
    name = "Probe P." if r % 2 else "Probe Pr."
    keys = ["⇧", "P", "⇧", "r", "o", "b", "e", None, "⇧", "P", "⇧"] + (["."] if r % 2 else ["r", "."])
    space = k.ev("document.querySelector('#osk-text .osk-space').textContent")
    ok = type_on_osk(k, [space if x is None else x for x in keys])
    typed = k.ev("document.getElementById('osk-input').value")
    check("osk: types with Shift", ok and typed == name, typed)
    k.tap("#osk-done")
    k.wait("false", 1)
    check("osk: closed, name in the field", not k.visible("#osk-modal") and k.ev("document.getElementById('nickname-input').value") == name,
          k.ev("document.getElementById('nickname-input').value"))
    check("osk: no navigation, no swap", k.ev("window.samePage") == 1 and k.ev("window.swaps") == s0)
    k.tap(".primary-action")
    k.wait("false", 2)
    saved = sql(f"select m.nickname || '|' || c.name from coffee_member m join coffee_coffeesort c on c.id = m.favourite_coffee_id where m.uid = {UID}")
    check("settings: saved", saved == f"{name}|{target}", saved)

    step("order pick/cancel")
    # Order: pick another sort, logout countdown, cancel
    k.nav("order")
    before = sql(f"select count(*) from coffee_order where member_id = {UID}")
    scan()
    check("order: logged in", k.wait("document.querySelector('input[name=coffee_id]')"))
    k.wait("false", 1)
    k.tap(".coffee-list .coffee-option .sort-text span", k.visible_unpicked(".coffee-list", "coffee_id"))
    check("order: logout countdown after a pick", k.wait("(document.querySelector('.countdown') || {textContent: ''}).textContent.includes('Abmeldung') || (document.querySelector('.countdown') || {textContent: ''}).textContent.includes('logout')", 8),
          k.ev("(document.querySelector('.countdown') || {}).textContent"))
    if k.ev("!!document.querySelector('.sort-pages.paged')"):
        # More sorts than fit: they come in pages - turn one, pick from it, then leave
        step("turn a page")
        s1 = k.ev("window.swaps")
        k.tap(".sort-page.current [data-sort-page]:last-child")
        k.wait("false", 1.5)
        check("pages: turned", k.ev("document.querySelector('.sort-page.current').dataset.page") != "0")
        check("pages: no swap on a turn", k.ev("window.swaps") == s1, f"{s1} -> {k.ev('window.swaps')}")
        name = k.ev("document.querySelector('.sort-page.current .coffee-option .sort-text span').textContent")
        k.tap(".sort-page.current .coffee-option .sort-text span", name)
        k.wait("false", 1.5)
        check("pages: picked on the turned page", k.ev("document.querySelector('input[name=coffee_id]:checked').parentElement.innerText").startswith(name), name)
        step("cancel after turning a page")
    k.tap(".action-row button.danger")
    check("order: cancel logs out", k.wait("document.getElementById('panel-state') && !document.querySelector('input[name=coffee_id]')", 5))
    check("order: cancel books nothing", sql(f"select count(*) from coffee_order where member_id = {UID}") == before)

    step("order now")
    # Order: Order now
    scan()
    k.wait("document.querySelector('input[name=coffee_id]')")
    k.wait("false", 1)
    k.tap(".primary-action")
    k.wait("false", 2)
    check("order: Order now books one", int(sql(f"select count(*) from coffee_order where member_id = {UID}")) == int(before) + 1)

    step("pay")
    # Pay: numeric keyboard, method tile, pay
    k.wait("false", 3)
    step("thank-you over, nav to pay")
    k.nav("info")
    k.wait("false", 2)
    step("pay screen")
    paid = sql(f"select count(*) from coffee_payment where member_id = {UID}")
    scan()
    check("pay: logged in", k.wait("document.getElementById('pay-amount-input')"))
    k.wait("false", 1)
    k.tap("[data-osk-numeric]")
    k.wait("getComputedStyle(document.getElementById('osk-numeric')).visibility === 'visible'", 5)
    k.ev("document.getElementById('osk-input').value = ''; 1")
    type_on_osk(k, ["1", ".", "5"], numeric=True)
    k.tap("#osk-done")
    k.wait("false", 1)
    check("pay: amount from the keyboard", k.ev("document.getElementById('pay-amount-input').value") == "1.5")
    if k.ev("!!document.querySelector('input[name=method]')"):
        k.tap(".coffee-grid .coffee-option span", "PayPal Test")
        check("pay: method tile picks", "PayPal" in (k.ev("(document.querySelector('input[name=method]:checked') || {value: ''}).value") or ""))
    k.tap("form .primary-action, form button[type=submit].success")
    k.wait("false", 2)
    check("pay: payment booked", int(sql(f"select count(*) from coffee_payment where member_id = {UID}")) == int(paid) + 1)

    step("ranking")
    # Ranking and back
    k.wait("false", 2)
    if k.ev("!document.querySelector('a.nav-btn[data-mode=ranking]').hidden"):
        check("ranking: opens", k.nav("ranking"))
        k.wait("false", 4)
        step("ranking shown")
    step("back to order")
    k.nav("order")
    k.wait("false", 3)
    step("end of round")

    c1 = crashes()
    check(f"round {r}: no renderer crash", c1 == c0, f"{c1 - c0} crash(es)")
    if c1 != c0:
        k.connect()
        k.ev("window.swaps = 0; document.body.addEventListener('htmx:afterSwap', e => { if (e.target.id === 'panel') window.swaps++; }); window.samePage = 1; 1")

print(json.dumps({"passed": sum(ok for _, ok in results), "failed": [n for n, ok in results if not ok],
                  "crashes": crashes() - start_crashes}))
