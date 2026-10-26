"""Takes the manual's screenshots of kiosk, dashboard and portal - runs ON the Pi, in the
kiosk's own Chromium (debug port on), next to the pi-device skill's cdp.py and demo_data.py
in /tmp. capture.sh drives it; run it by hand only for a subset:

    python3 -I capture_web.py de en                 everything, both languages
    python3 -I capture_web.py de --only kiosk       one area: kiosk | dashboard | portal

Dashboard and portal can also be taken from the dev machine, in a local headless Chromium
(capture.sh does that): CAPTURE_BASE is then the Pi's address, CDP_PORT that Chromium's debug
port, CAPTURE_SSH the ssh command that reaches the Pi for its Django shell, and CAPTURE_OUT
where the images go.

Writes /tmp/manual/<lang>/<name>.png. The kiosk shots log the real kiosk in through demo
cards (demo_data.py scan) and log it out again through a nav tap; the dashboard is used as
the admin from /opt/coffeepy/.env. The language comes from the language cookie, like on any
device. Expects demo_data.py seed to have run.
"""
import base64
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.environ.get("CDP_PATH", "/tmp"))
import cdp  # noqa: E402

BASE = os.environ.get("CAPTURE_BASE", "https://localhost:8443")
OUT = os.environ.get("CAPTURE_OUT", "/tmp/manual")
COMPOSE = "sudo docker compose -f /opt/coffeepy/docker-compose.yml"
SSH = os.environ.get("CAPTURE_SSH", "")  # empty: running on the Pi itself


def on_pi(command, stdin=None):
    if SSH:
        command = f"{SSH} {json.dumps(command)}"
    return subprocess.run(command, shell=True, stdin=stdin, capture_output=True, text=True).stdout


def django(*args):
    """demo_data.py (in /tmp on the Pi) inside the web container."""
    return on_pi(f"{COMPOSE} exec -T -e SCRIPT_ARGS='{' '.join(args)}' web python manage.py shell < /tmp/demo_data.py")


def shell_django(code):
    out = on_pi(f"{COMPOSE} exec -T web python manage.py shell -c {json.dumps(code)}").strip()
    return out.splitlines()[-1] if out else ""


class Browser:
    def __init__(self, lang):
        self.lang = lang
        self.page = cdp.Page()
        for domain in ("Page", "Network", "Runtime"):
            self.page.call(f"{domain}.enable")
        os.makedirs(f"{OUT}/{lang}", exist_ok=True)

    def viewport(self, width=None, height=None, scale=1, mobile=False):
        if width is None:
            self.page.call("Emulation.clearDeviceMetricsOverride")
        else:
            self.page.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                           deviceScaleFactor=scale, mobile=mobile)

    def language(self):
        self.page.call("Network.setCookie", name="django_language", value=self.lang,
                       url=BASE + "/", secure=True, sameSite="Lax")

    def go(self, path):
        self.page.call("Page.navigate", url=BASE + path)
        self.page.collect(0.8, until="Page.loadEventFired")

    def js(self, expression):
        return self.page.eval(expression)

    def wait(self, expression, timeout=15):
        # !!(...): an expression ending in a DOM node (querySelector) comes back over the
        # protocol as nothing at all - it would never count as true.
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"!!({expression})") is True:
                return True
            time.sleep(0.2)
        raise RuntimeError(f"timed out waiting for: {expression}")

    def state(self):
        return self.js("(document.getElementById('panel-state')||{dataset:{}}).dataset.panelKey || ''")

    def wait_state(self, prefix):
        self.wait(f"((document.getElementById('panel-state')||{{dataset:{{}}}}).dataset.panelKey||'').startsWith({json.dumps(prefix)})")
        time.sleep(1.2)  # clock filled in, animations under way

    def click(self, selector):
        self.js(f"document.querySelector({json.dumps(selector)}).click()")

    def open_modal(self, selector):
        """Clicks a button that fills the QR modal. Retried: a panel poll that lands at the
        same moment replaces the button, and the click then goes to a detached element."""
        ready = "document.getElementById('qr-modal').classList.contains('open') && document.querySelector('#qr-modal-content svg')"
        for _ in range(3):
            self.click(selector)
            try:
                return self.wait(ready, timeout=6)
            except RuntimeError:
                self.js("document.getElementById('qr-modal').classList.remove('open')")
        raise RuntimeError(f"modal never opened from {selector}")

    def shot(self, name, settle=0.6, full=False):
        """full: the whole page, not only what fits the window - for pages whose point is
        further down (the update page's version list)."""
        time.sleep(settle)
        if full:
            width = self.js("window.innerWidth")
            height = self.js("Math.ceil(document.documentElement.scrollHeight)")
            self.viewport(width, height)
            time.sleep(0.4)
        data = self.page.call("Page.captureScreenshot", format="png")["data"]
        if full:
            self.viewport(width, 800)
        with open(f"{OUT}/{self.lang}/{name}.png", "wb") as f:
            f.write(base64.b64decode(data))
        print(f"  {self.lang}/{name}.png")


def kiosk(b):
    b.viewport()
    b.go("/kiosk/")
    for mode, name in (("order", "kiosk-wait-order"), ("info", "kiosk-wait-pay"), ("settings", "kiosk-wait-settings")):
        b.click(f"a.nav-btn[data-mode={mode}]")
        b.wait_state("wait_for_card")
        b.shot(name)

    b.open_modal(".admin-btn")
    b.shot("kiosk-setup-popup")
    b.js("document.getElementById('qr-modal').classList.remove('open')")

    # The red warning sign: an RTC check that found no clock module (written by hand here, the
    # next real check - started right after - puts the true state back).
    on_pi("echo '{\"state\": \"missing\", \"detail\": \"manual screenshot\", \"checked\": '$(date +%s)'}' "
          "| sudo tee /opt/coffeepy/data/storage/rtc.json >/dev/null")
    b.wait("document.querySelector('.device-alarms')", timeout=10)
    b.shot("kiosk-device-alarm", settle=0.3)
    on_pi("sudo systemctl start coffeepy-rtc-sync.service")

    b.click("a.nav-btn[data-mode=order]")
    b.wait_state("wait_for_card")
    django("scan", "Anna")
    b.wait_state("order:")
    b.shot("kiosk-order")
    b.click("button[hx-post*='order/confirm']")
    b.wait_state("thank_you")
    b.shot("kiosk-thank-you", settle=1.5)

    b.click("a.nav-btn[data-mode=info]")
    b.wait_state("wait_for_card")
    django("scan", "David")
    b.wait_state("info_pay")
    b.shot("kiosk-pay")

    b.click("a.nav-btn[data-mode=settings]")
    b.wait_state("wait_for_card")
    django("scan", "Anna")
    b.wait_state("settings")
    b.shot("kiosk-settings")
    b.open_modal("button[hx-get*='link-phone']")
    b.shot("kiosk-link-phone")
    b.js("document.getElementById('qr-modal').classList.remove('open')")
    b.go(b.js("document.querySelector('a.osk-open-btn').getAttribute('href')"))
    b.shot("kiosk-keyboard")
    b.go("/kiosk/")

    b.click("a.nav-btn[data-mode=settings]")  # a nav tap logs out
    b.wait_state("wait_for_card")
    django("scan", "new")
    b.wait_state("settings")
    b.shot("kiosk-new-card")

    b.click("a.nav-btn[data-mode=ranking]")
    b.wait_state("ranking")
    b.shot("kiosk-ranking-alltime")
    for view in ("month", "pillory"):
        b.click(f".ranking-tabs a[href$='/ranking/{view}/']")
        b.wait(f"document.querySelector(\".ranking-tabs a.active[href$='/ranking/{view}/']\")")
        b.shot(f"kiosk-ranking-{view}")

    b.click("a.nav-btn[data-mode=order]")
    b.wait_state("wait_for_card")
    on_pi("sudo bash -c '. /opt/coffeepy/install/maintenance.sh; maintenance_begin update; maintenance_step images 35'")
    b.wait("!document.getElementById('maintenance').hidden", timeout=10)
    b.shot("kiosk-maintenance", settle=1.2)
    on_pi("sudo rm -f /opt/coffeepy/data/maintenance/status.json")
    b.page.collect(1, until="Page.loadEventFired")  # maintenance.js reloads the page


DASHBOARD_PAGES = [
    ("/dashboard/", "dashboard-home"), ("/dashboard/statistics/", "dashboard-statistics"),
    ("/dashboard/pillory/", "dashboard-pillory"), ("/dashboard/members/", "dashboard-members"),
    ("/dashboard/coffee-sorts/", "dashboard-coffee-sorts"), ("/dashboard/payment-methods/", "dashboard-payment-methods"),
    ("/dashboard/payment-methods/add/", "dashboard-payment-method-form"), ("/dashboard/certificate/", "dashboard-certificate"),
    ("/dashboard/update/", "dashboard-update"), ("/dashboard/backup/", "dashboard-backup"),
    ("/dashboard/system/", "dashboard-system"), ("/dashboard/system-time/", "dashboard-system-time"),
    ("/dashboard/import/", "dashboard-import"), ("/dashboard/audit-log/", "dashboard-audit-log"),
    ("/dashboard/members/merge/", "dashboard-merge"), ("/dashboard/reset/", "dashboard-reset"),
    ("/dashboard/bookings/", "dashboard-bookings"), ("/dashboard/bookings/payments/add/", "dashboard-payment-form"),
    ("/dashboard/users/", "dashboard-users"), ("/dashboard/users/add/", "dashboard-user-form"),
    ("/dashboard/login-rules/", "dashboard-password-policy"), ("/dashboard/account/", "dashboard-account"),
    ("/dashboard/backup/restore/upload/", "dashboard-restore-upload"),
]
FULL_PAGE = ("dashboard-update", "dashboard-backup")


def dashboard(b, password):
    b.viewport(1280, 800)
    b.go("/dashboard/login/")
    b.shot("dashboard-login")
    b.js(f"document.querySelector('input[name=username]').value='admin';"
         f"document.querySelector('input[name=password]').value={json.dumps(password)};"
         f"document.querySelector('form').submit()")
    b.page.collect(0.5, until="Page.loadEventFired")
    anna = shell_django("from coffee.models import Member; print(Member.objects.get(nickname='Anna').uid)")
    for path, name in DASHBOARD_PAGES + [(f"/dashboard/members/{anna}/edit/", "dashboard-member-form")]:
        b.go(path)
        b.shot(name, settle=1.5 if "statistics" in path or "system" in path else 0.6, full=name in FULL_PAGE)
    b.viewport(390, 844, scale=2, mobile=True)  # the same dashboard on a phone
    b.go("/dashboard/")
    b.shot("dashboard-phone")
    b.viewport(1280, 800)
    b.js("fetch('/dashboard/logout/', {method: 'POST', headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}})")
    time.sleep(1)


def portal(b):
    b.viewport(390, 844, scale=2, mobile=True)
    b.go("/portal/link/expired-example/")
    b.shot("portal-link-invalid")
    token = shell_django(
        "from datetime import timedelta; from django.utils import timezone; "
        "from coffee.models import Member, MemberLinkToken; "
        "print(MemberLinkToken.objects.create(member=Member.objects.get(nickname='David'), "
        "expires_at=timezone.now() + timedelta(minutes=5)).token)")
    b.go(f"/portal/link/{token}/")
    b.shot("portal-home")
    for path, name in (("/portal/pay/", "portal-pay"), ("/portal/stats/", "portal-stats"),
                       ("/portal/cards/", "portal-cards"), ("/portal/devices/", "portal-devices")):
        b.go(path)
        b.shot(name, settle=1.5 if "stats" in path else 0.6)


def main():
    langs = [a for a in sys.argv[1:] if a in ("de", "en")]
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    password = on_pi("sudo grep ^DJANGO_SUPERUSER_PASSWORD= /opt/coffeepy/.env | cut -d= -f2-").strip()
    for lang in langs:
        b = Browser(lang)
        b.language()
        if only is None or "kiosk" in only:
            kiosk(b)
        if only is None or "dashboard" in only:
            dashboard(b, password)
        if only is None or "portal" in only:
            portal(b)
        b.viewport()
    # Back to how the kiosk was: no language cookie (its default again), no dashboard or
    # portal session left in its browser, its own screen.
    page = cdp.Page()
    page.call("Network.clearBrowserCookies")
    if not SSH:
        page.call("Page.navigate", url=BASE + "/kiosk/")


if __name__ == "__main__":
    main()
