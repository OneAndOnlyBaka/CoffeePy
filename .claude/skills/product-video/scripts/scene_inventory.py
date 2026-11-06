import subprocess, sys, time
sys.path.insert(0, sys.path[0])
from rec import APP, ENV, PY, Browser, Recorder, django, scan

subprocess.run([PY, "manage.py", "shell"], cwd=APP, env=dict(ENV, SCRIPT_ARGS="inventory on"),
               stdin=open(f"{APP}/.claude/skills/user-manual/scripts/demo_data.py"), capture_output=True)
django("from coffee.models import InventoryItem as I; "
       "[I.objects.filter(name__endswith=e).update(name=e) for e in ('Coffee beans', 'Milk', 'Cookies')]; print('ok')")


def typing(b, selector, text):
    b.tap(selector)
    time.sleep(0.3)
    for ch in text:
        b.page.call("Input.insertText", text=ch)
        time.sleep(0.18)


b = Browser()
b.viewport(1024, 600)
b.go("/kiosk/")
b.tap("a.nav-btn[data-mode=order]", pause=0)
b.wait("!document.querySelector('a.nav-btn[data-mode=purchase]').hidden")
b.tap("a.nav-btn[data-mode=purchase]", pause=0)
b.wait_state("wait_for_card")
time.sleep(1.5)

r = Recorder("kiosk_inventory")
time.sleep(2.0)
scan("Anna")
b.wait_state("purchase")
time.sleep(1.2)
b.tap(".section-purchase label.coffee-option")
time.sleep(0.6)
typing(b, "#purchase-price-input", "16.99")
time.sleep(1.5)
b.tap("label.coffee-option:has(#section-stock)")
time.sleep(1.2)
b.tap(".section-stock label.coffee-option")
time.sleep(0.6)
typing(b, "#stock-count-input", "1.2")
time.sleep(2.0)
r.stop()
b.tap("a.nav-btn[data-mode=order]", pause=0)

b.viewport(1280, 800)
b.go("/dashboard/inventory/")
time.sleep(1.5)
r = Recorder("dashboard_inventory")
time.sleep(3.0)
b.go("/dashboard/inventory/costs/")
time.sleep(2.0)
b.scroll(500, duration=1.8)
time.sleep(1.5)
b.go("/dashboard/inventory/spending/")
time.sleep(3.5)
r.stop()

django("from dashboard.models import InventoryPolicy as P; P.objects.update(mode=P.MODE_OFF); print('off')")
