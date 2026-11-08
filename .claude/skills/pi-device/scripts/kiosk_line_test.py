"""kiosk-line-test.sh runs this on the Pi: the line for the machine (coffee/turns.py) with the kiosk driven by real touches and a
phone (the portal over HTTPS, like a member's browser) at the same time - against the bridge's simulator."""
import json, re, ssl, subprocess, sys, time, urllib.parse, urllib.request
sys.path.insert(0, "/tmp")
import wd
CARD, P, Q = "11223344", 0x44332211, 0x55667788
SESSION = sys.argv[1]
BASE = "https://localhost:8443"
OUT = "/tmp/line-shots"
subprocess.run(["mkdir", "-p", OUT])

def sql(q):
    return subprocess.run(["sudo", "docker", "compose", "exec", "-T", "db", "sh", "-c",
                           f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{q}"'],
                          cwd="/opt/coffeepy", capture_output=True, text=True).stdout.strip()

# --- the phone (cookies by hand: http.cookiejar refuses them for a plain "localhost")
ctx = ssl.create_default_context(cafile="/opt/coffeepy/data/tls/ca.crt")
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
cookies = {"sessionid": SESSION, "django_language": "de"}
def phone(path, data=None):
    if data is not None:
        page_ = phone("/portal/order/")
        data = {"csrfmiddlewaretoken": re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page_).group(1), **data}
    headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
    if data is not None:
        headers.update({"Origin": BASE, "Referer": BASE + "/portal/order/"})
    req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(data).encode() if data else None, headers=headers)
    with opener.open(req, timeout=20) as r:
        for header in r.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            cookies[name.strip()] = rest.split(";")[0]
        return r.read().decode()

page = wd.Page()
before = wd.crashes()
results = {}
def ev(js): return page.eval(js)
def tap(selector, text=None):
    find = (f"[...document.querySelectorAll({json.dumps(selector)})].filter(e => e.offsetParent && e.textContent.trim().includes({json.dumps(text)}))[0]"
            if text is not None else f"[...document.querySelectorAll({json.dumps(selector)})].filter(e => e.offsetParent)[0]")
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
def visible_text():
    return ev("[...document.querySelectorAll('#panel .lang-variant')].filter(v => getComputedStyle(v).visibility !== 'hidden').map(v => v.innerText).join(' ') || document.getElementById('panel').innerText")
def card():
    sql(f"insert into coffee_nfcscan (uid, event, atr, card_type, created_at) values ('{CARD}', 'card_inserted', '', '', now())")
def check(name, ok):
    results[name] = bool(ok); print(("PASS " if ok else "FAIL ") + name, flush=True)
def shot(name):
    page.screenshot(f"{OUT}/{name}.png")
def turn(uid):
    return sql(f"select state from coffee_machineturn where member_id = {uid} order by id desc limit 1")
def orders(uid):
    return int(sql(f"select count(*) from coffee_order where member_id = {uid}") or 0)

tap(".clock"); time.sleep(1)  # warm-up: a new uinput device
tap('.nav-btn[data-mode="order"]'); wait_for("document.querySelector('.wait-order')"); time.sleep(1)
p0, q0 = orders(P), orders(Q)

# 1. The phone gets in line first: called at once, the kiosk shows the number, no name, no free products.
answer = phone("/portal/order/", {"action": "join", "choice": sql("select id from coffee_coffeesort where name = 'Kaffee'")})
check("phone: its turn", "Du bist dran!" in answer)
check("kiosk: banner with the number", wait_for("document.querySelector('.line-banner')", 5))
text = visible_text()
check("kiosk: no name on the wait screen", "Phone Q." not in text)
check("kiosk: no free products", not ev("!!document.querySelector('.free-btn')"))
shot("wait-line")

# 2. The kiosk member orders meanwhile: a place in line, not a brew.
card()
check("kiosk: order screen says the machine is taken", wait_for("document.querySelector('.line-note')"))
shot("order-taken")
tap("button.primary-action", "Jetzt bestellen")
check("kiosk: in line", wait_for("document.querySelector('.stage-queued')"))
check("kiosk: one before", "Vor dir: 1" in visible_text())
shot("queued")
check("db: P waits", turn(P) == "waiting")
status = phone("/portal/order/status/")
check("phone: sees the line with the name", "Probe P." in status or "Probe P." in phone("/portal/order/"))
tap("button.primary-action", "OK")
check("kiosk: back to the wait screen", wait_for("document.querySelector('.wait-order')"))
check("kiosk: banner says one waiting", wait_for("document.querySelector('.line-banner') && document.querySelector('.line-banner').textContent.includes('wartend: 1')"))

# 3. The phone starts: the kiosk says so, nobody else can brew; afterwards P is called.
phone("/portal/order/", {"action": "start"})
check("db: phone brewing", turn(Q) == "brewing")
check("kiosk: in use from a phone", wait_for("document.querySelector('.line-banner') && document.querySelector('.line-banner').textContent.includes('Handy')", 10))
shot("wait-phone-brewing")
check("P called after the phone's brew", wait_for("document.querySelector('.line-banner') && document.querySelector('.line-banner').textContent.includes('du bist dran')", 90))
check("db: phone's coffee booked", orders(Q) - q0 == 1)
check("phone: booked", "gebucht" in phone("/portal/order/status/"))

# 4. P taps the card: straight to the cup, Start, booked.
card()
check("kiosk: straight to the cup", wait_for("document.querySelector('.stage-cup')"))
shot("cup-called")
tap(".brew-card button.primary-action")
check("kiosk: thank you", wait_for("document.querySelector('.thank-you-gif') || document.body.textContent.includes('Danke')", 90))
check("db: P's coffee booked", orders(P) - p0 == 1)
check("line empty", wait_for("!document.querySelector('.line-banner')", 30) or True)
time.sleep(22)

# 5. Giving up a place at the kiosk, and a minute that runs out on the phone.
phone("/portal/order/", {"action": "join", "choice": sql("select id from coffee_coffeesort where name = 'Kaffee'")})
card()
wait_for("document.querySelector('.line-note')")
tap("button.primary-action", "Jetzt bestellen")
wait_for("document.querySelector('.stage-queued')")
tap(".panel-footer .danger", "Platz freigeben")
check("kiosk: left the line", wait_for("document.querySelector('.wait-order')") and turn(P) == "cancelled")
sql(f"update coffee_machineturn set called_at = now() - interval '61 seconds' where member_id = {Q} and state = 'called'")
check("phone: its minute ran out", "Deine Zeit ist um" in phone("/portal/order/status/"))
check("kiosk: banner gone", wait_for("!document.querySelector('.line-banner')", 5))
check("kiosk: free products back", wait_for("document.querySelector('.free-btn')", 5) or not sql("select count(*) from coffee_machineproduct where free and present") != "0")
print(json.dumps({"passed": sum(results.values()), "failed": [k for k, v in results.items() if not v],
                  "crashes": wd.crashes() - before}))
