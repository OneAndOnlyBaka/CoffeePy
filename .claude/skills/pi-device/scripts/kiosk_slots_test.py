"""kiosk-slots-test.sh runs this on the Pi: "my coffees" with real touches - set two slots in the editor, order the 2-cup slot (the
simulator brews both), then the order screen with the sorts hidden. Screenshots through WebDriver."""
import json, subprocess, sys, time
sys.path.insert(0, "/tmp")
import wd
CARD, UID = "11223344", 0x44332211
OUT = "/tmp/slots-shots"
subprocess.run(["mkdir", "-p", OUT])

def sql(q):
    return subprocess.run(["sudo", "docker", "compose", "exec", "-T", "db", "sh", "-c",
                           f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{q}"'],
                          cwd="/opt/coffeepy", capture_output=True, text=True).stdout.strip()

page = wd.Page()
before = wd.crashes()
results = {}
def ev(js): return page.eval(js)
def tap(selector, text=None, index=0):
    find = (f"[...document.querySelectorAll({json.dumps(selector)})].filter(e => e.textContent.trim().includes({json.dumps(text)}))[{index}]"
            if text is not None else f"document.querySelectorAll({json.dumps(selector)})[{index}]")
    box = ev(f"(e => e && (r => [r.left + r.width / 2, r.top + r.height / 2])(e.getBoundingClientRect()))({find})")
    if not box:
        print("no element", selector, text); return False
    page.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": box[0], "y": box[1]}]); time.sleep(0.05)
    page.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[]); time.sleep(0.25)
    return True
def wait_for(js, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        if ev(f"!!({js})"): return True
        time.sleep(0.2)
    return False
def card():
    sql(f"insert into coffee_nfcscan (uid, event, atr, card_type, created_at) values ('{CARD}', 'card_inserted', '', '', now())")
def check(name, ok):
    results[name] = bool(ok); print(("PASS " if ok else "FAIL ") + name, flush=True)
def shot(name):
    page.screenshot(f"{OUT}/{name}.png")

tap(".clock"); time.sleep(1)  # warm-up: a new uinput device
# --- the editor
tap('.nav-btn[data-mode="settings"]'); wait_for("document.querySelector('.wait-settings')")
card()
check("settings: logged in", wait_for("document.querySelector('input[name=nickname]')"))
tap("button", "Meine Kaffees")
check("editor opens", wait_for("document.querySelector('.slots-editor')"))
time.sleep(1.5)
check("editor stays", ev("!!document.querySelector('.slots-editor')") or print("now:", ev("document.getElementById('panel-state').dataset.panelKey + ' ' + document.querySelector('#panel h1').textContent")))
shot("editor-empty")
c1 = '.slot-card[data-pos="1"] '
if not results["editor stays"]:
    print(json.dumps({"failed": [k for k, v in results.items() if not v], "crashes": wd.crashes() - before})); sys.exit()
for _ in range(2):
    tap(c1 + '[data-step="sort"][data-by="1"]')
check("slot 1: Kaffee", ev(f"document.querySelector('{c1}[data-show=sort]').textContent") == "Kaffee")
for _ in range(3): tap(c1 + '[data-step="strength"][data-by="1"]')
for _ in range(10): tap(c1 + '[data-step="water"][data-by="1"]')
tap(c1 + '[data-step="count"][data-by="1"]')
check("slot 1: 7, 200 ml, 2x", ev(f"[...document.querySelectorAll('{c1}[data-show]')].map(e => e.textContent).join('|')") == "Kaffee|7|200 ml|extra heiß|2 ×")
c2 = '.slot-card[data-pos="2"] '
tap(c2 + '[data-step="sort"][data-by="1"]')
tap(c2 + '[data-step="strength"][data-by="-1"]'); tap(c2 + '[data-step="temperature"][data-by="1"]')
check("slot 2: Espresso", ev(f"document.querySelector('{c2}[data-show=sort]').textContent") == "Espresso")
shot("editor-filled")
tap("button.primary-action")
check("saved", wait_for("document.querySelector('.slots-editor .msg.success')"))
check("db: slot 1", sql(f"select strength || ',' || water_ml || ',' || count from coffee_memberslot where member_id = {UID} and position = 1") == "7,200,2")
time.sleep(1)
# --- ordering with the slot: 2 cups in a row
tap('.nav-btn[data-mode="order"]'); wait_for("document.querySelector('.wait-order')")
time.sleep(1)
orders0 = int(sql(f"select count(*) from coffee_order where member_id = {UID}") or 0)
card()
check("order: my coffees row", wait_for("document.querySelector('.slot-row input[value=\"slot:1\"]')"))
shot("order-slots")
tap(".slot-option", "Kaffee")
tap("button.primary-action")
check("cup screen: 2 in a row", wait_for("document.querySelector('.brew-card.stage-cup') && document.body.textContent.includes('2 × Kaffee')"))
shot("cup-2x")
tap(".brew-card button.primary-action")
check("cup 2 of 2 shows", wait_for("document.body.textContent.includes('Tasse 2 von 2')", 60))
shot("cup-2-of-2")
check("thank you after both", wait_for("document.querySelector('.thank-you-gif') || document.body.textContent.includes('Danke')", 60))
check("two orders booked", int(sql(f"select count(*) from coffee_order where member_id = {UID}") or 0) - orders0 == 2)
print("info:", sql(f"select info from coffee_order where member_id = {UID} order by id desc limit 1"))
time.sleep(22)
# --- the sorts hidden behind the slots
sql(f"update coffee_member set hide_standard_sorts = true where uid = {UID}")
card()
check("hidden: only my coffees", wait_for("document.querySelector('.sort-page.current[data-page=\"0\"] .slot-option')"))
shot("order-hidden")
tap(".sort-page.current .sort-nav", "Andere Sorten")
check("other sorts one page on", wait_for("document.querySelector('.sort-page.current[data-page=\"1\"]')"))
tap(".action-row .danger")
wait_for("document.querySelector('.wait-card')")
print(json.dumps({"passed": sum(results.values()), "failed": [k for k, v in results.items() if not v],
                  "crashes": wd.crashes() - before}))
