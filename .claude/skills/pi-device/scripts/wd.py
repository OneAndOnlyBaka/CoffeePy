"""cdp.py's Page for the kiosk's WPE WebKit (cog): the UI tests and kiosk-browser.sh on a cog device.
Runs ON the Pi (stdlib only), as root (uinput), next to cdp.py:

    KIOSK_BROWSER=wpe python3 -I /tmp/kiosk_ui_test.py 4
    python3 -I /tmp/wd.py 'document.title'          # an expression's value
    python3 -I /tmp/wd.py shot /tmp/kiosk.png       # WebDriver's screenshot
    python3 -I /tmp/wd.py cookie                    # the kiosk's cookies, sessionid included

- The page through WPEWebDriver (W3C WebDriver on localhost:4444, started by kiosk-browser.sh on
  in place of the kiosk session): its session launches cog itself, with --automation, straight on
  DRM/KMS like the real session; the session id is kept in SESSION_FILE, so successive calls
  share one cog and the page's state.
- Taps through a virtual touchscreen (uinput) with the real one's capabilities (QDTECH MPI7002: MT
  slots, BTN_TOUCH, INPUT_PROP_DIRECT): libinput, cog and WebKit see what a finger would make.
  WebDriver's own pointer actions would skip the input stack, where kiosk bugs have lived before.
- crashes(): WPEWebProcess deaths in the journal since this module was loaded - the kernel's
  "unhandled ... fault" line for a SIGSEGV, or cog's own "Crash!: The renderer process crashed".
"""
import base64
import fcntl
import json
import os
import struct
import subprocess
import time
import urllib.error
import urllib.request

DRIVER = "http://127.0.0.1:4444"
URL = "https://localhost:8443/kiosk/"
WIDTH, HEIGHT = 1024, 600
COG_ARGS = ["--automation", "-P", "drm", "-O", "renderer=gles"]
SINCE = time.strftime("%Y-%m-%d %H:%M:%S")


def _request(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(DRIVER + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("value")
    except urllib.error.HTTPError as e:
        value = json.load(e).get("value", {})
        raise RuntimeError(f"{value.get('error')}: {value.get('message', '')[:200]}") from None


# --- the virtual touchscreen ---------------------------------------------------------------------
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_TOUCH = 0x14A
ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0, 1, 0x2F, 0x35, 0x36, 0x39
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT = 0x40045564, 0x40045565, 0x40045567, 0x4004556E
UI_DEV_CREATE = 0x5501
INPUT_PROP_DIRECT = 1


class Touch:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_SYN, EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        fcntl.ioctl(self.fd, UI_SET_KEYBIT, BTN_TOUCH)
        for code in (ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        absmax, absmin = [0] * 64, [0] * 64
        absmax[ABS_X] = absmax[ABS_MT_POSITION_X] = WIDTH - 1
        absmax[ABS_Y] = absmax[ABS_MT_POSITION_Y] = HEIGHT - 1
        absmax[ABS_MT_SLOT] = 4
        absmax[ABS_MT_TRACKING_ID] = 65535
        # struct uinput_user_dev: name[80], input_id (4 x u16), ff_effects_max, absmax/min/fuzz/flat[64]
        dev = struct.pack("80sHHHHi", b"coffeepy-test-touch", 3, 0x0484, 0x5750, 1, 0)
        dev += struct.pack("64i", *absmax) + struct.pack("64i", *absmin) + struct.pack("128i", *([0] * 128))
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.tracking = 0
        time.sleep(1.5)  # libinput picks the new device up through udev

    def _emit(self, *events):
        now = time.time()
        sec, usec = int(now), int((now % 1) * 1e6)
        os.write(self.fd, b"".join(struct.pack("qqHHi", sec, usec, t, c, v) for t, c, v in events)
                 + struct.pack("qqHHi", sec, usec, EV_SYN, 0, 0))

    def down(self, x, y):
        self.tracking += 1
        x, y = max(0, min(WIDTH - 1, round(x))), max(0, min(HEIGHT - 1, round(y)))
        self._emit((EV_ABS, ABS_MT_SLOT, 0), (EV_ABS, ABS_MT_TRACKING_ID, self.tracking),
                   (EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y),
                   (EV_KEY, BTN_TOUCH, 1), (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y))

    def move(self, x, y):
        x, y = max(0, min(WIDTH - 1, round(x))), max(0, min(HEIGHT - 1, round(y)))
        self._emit((EV_ABS, ABS_MT_SLOT, 0), (EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y),
                   (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y))

    def up(self):
        self._emit((EV_ABS, ABS_MT_SLOT, 0), (EV_ABS, ABS_MT_TRACKING_ID, -1), (EV_KEY, BTN_TOUCH, 0))


_touch = []
SESSION_FILE = "/run/coffeepy-wd-session"  # one WebDriver session across calls: a new one is a new cog


def session():
    """The WebDriver session (and with it cog) - the one kiosk-browser.sh on or an earlier call
    opened, while it still answers, or a new one."""
    try:
        with open(SESSION_FILE) as f:
            sid = f.read().strip()
        _request("GET", f"/session/{sid}/url", timeout=10)
        return sid
    except (OSError, RuntimeError, urllib.error.URLError):
        pass
    value = _request("POST", "/session", {"capabilities": {"alwaysMatch": {
        "wpe:browserOptions": {"binary": "/usr/bin/cog", "args": COG_ARGS}}}}, timeout=120)
    sid = value["sessionId"]
    _request("POST", f"/session/{sid}/url", {"url": URL}, timeout=120)
    with open(SESSION_FILE, "w") as f:
        f.write(sid)
    return sid


class Page:
    """What kiosk_ui_test.py and kiosk_machine_test.py use of cdp.Page: eval, and call() for
    Page.enable and Input.dispatchTouchEvent."""

    def __init__(self):
        self.sid = session()
        # A crashed web process leaves cog on an empty view; the next page load brings it back,
        # as the kiosk's own restart would.
        if not self.eval("!!document.getElementById('panel')"):
            _request("POST", f"/session/{self.sid}/url", {"url": URL}, timeout=120)

    def eval(self, expr):
        # A global eval, like CDP's Runtime.evaluate: statements allowed, the last one's value back.
        return _request("POST", f"/session/{self.sid}/execute/sync",
                        {"script": "return (0, eval)(arguments[0]);", "args": [expr]})

    def call(self, method, **params):
        """The few DevTools methods the tests and the manual's capture_web.py use, in WebDriver."""
        if method == "Page.navigate":
            _request("POST", f"/session/{self.sid}/url", {"url": params["url"]}, timeout=120)
        elif method == "Page.captureScreenshot":
            return {"data": _request("GET", f"/session/{self.sid}/screenshot")}
        elif method == "Network.setCookie":
            cookie = {"name": params["name"], "value": params["value"], "path": "/",
                      "secure": params.get("secure", False), "sameSite": params.get("sameSite", "Lax")}
            _request("POST", f"/session/{self.sid}/cookie", {"cookie": cookie})
        elif method == "Network.clearBrowserCookies":
            _request("DELETE", f"/session/{self.sid}/cookie")
        elif method == "Input.dispatchTouchEvent":
            if not _touch:  # created on the first tap: needs root, an eval does not
                _touch.append(Touch())
            touch = _touch[0]
            points = params.get("touchPoints") or []
            if params["type"] == "touchStart":
                touch.down(points[0]["x"], points[0]["y"])
            elif params["type"] == "touchMove":
                touch.move(points[0]["x"], points[0]["y"])
            else:
                touch.up()
        return {}

    def collect(self, seconds, until=None):
        """cdp.Page.collect's stand-in: WebDriver's navigate already waits for the load, and a
        page that reloads itself (maintenance.js) is waited for by its readyState."""
        time.sleep(seconds)
        end = time.time() + 15
        while time.time() < end:
            try:
                if self.eval("document.readyState") == "complete":
                    return []
            except RuntimeError:
                pass
            time.sleep(0.3)
        return []

    def screenshot(self, path):
        with open(path, "wb") as f:
            f.write(base64.b64decode(_request("GET", f"/session/{self.sid}/screenshot")))


def crashes():
    out = subprocess.run(["journalctl", "--since", SINCE, "--no-pager", "-o", "cat"],
                         capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines()
               if ("WPEWebProcess" in line and ("unhandled" in line or "segfault" in line or "dumped core" in line))
               or "renderer process crashed" in line)


if __name__ == "__main__":
    import sys
    page = Page()
    if sys.argv[1:2] == ["shot"]:
        page.screenshot(sys.argv[2])
        print("saved", sys.argv[2])
    elif sys.argv[1:2] == ["cookie"]:  # WebDriver sees HttpOnly cookies too - the session's sessionid
        for cookie in _request("GET", f"/session/{page.sid}/cookie"):
            print(f"{cookie['name']}={cookie['value']}")
    else:
        print(page.eval(sys.argv[1]))
