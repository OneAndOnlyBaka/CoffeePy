"""Talks to the kiosk's Chromium over the DevTools protocol. Runs ON the Pi (stdlib only -
the Pi has no websocket module), so copy it over and run it there; kiosk-browser.sh does
both. Needs Chromium started with --remote-debugging-port=9222 (kiosk-browser.sh on).

    python3 -I cdp.py errors          reload the page, print console errors + failed/!=200 requests
    python3 -I cdp.py eval 'JS'       evaluate an expression in the kiosk page, print the result
    python3 -I cdp.py taps MODE...    tap nav buttons with real touch events, time each one
    python3 -I cdp.py egg             tap the Doom sequence (and a wrong one) and report where it lands
    python3 -I cdp.py cookie          print the kiosk's sessionid (to replay its requests in the container)
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request

PORT = 9222


class Page:
    def __init__(self):
        tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
        pages = [t for t in tabs if t.get("type") == "page"]
        # The kiosk tab - or whatever page it was navigated to (chrome://policy, say).
        tab = next((t for t in pages if "localhost:8443" in t["url"]), None) or next(
            t for t in pages if not t["url"].startswith(("chrome://omnibox", "devtools://")))
        path = tab["webSocketDebuggerUrl"].split(str(PORT), 1)[1]
        self.sock = socket.create_connection(("localhost", PORT))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.send(
            f"GET {path} HTTP/1.1\r\nHost: localhost:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(4096)
        self.buf = self.buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0
        self.events = []

    def _read(self, n):
        while len(self.buf) < n:
            self.buf += self.sock.recv(65536)
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _send(self, obj):
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        header = bytes([0x81])
        if len(payload) < 126:
            header += bytes([0x80 | len(payload)])
        else:
            header += bytes([0x80 | 126]) + struct.pack(">H", len(payload))
        self.sock.send(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        _, b2 = self._read(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        return json.loads(self._read(n))

    def call(self, method, **params):
        self.next_id += 1
        self._send({"id": self.next_id, "method": method, "params": params})
        while True:
            msg = self.recv()
            if msg.get("id") == self.next_id:
                return msg.get("result", {})
            self.events.append(msg)

    def eval(self, expr):
        result = self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        if "exceptionDetails" in result:
            return f"<exception: {result['exceptionDetails'].get('exception', {}).get('description', '?')[:200]}>"
        return result.get("result", {}).get("value")

    def collect(self, seconds, until=None):
        """Gathers events for `seconds` - or, with `until` (an event name), until that event
        has arrived and `seconds` more have passed. A fixed wait alone is useless on a Pi 3:
        a reload right after the kiosk session started took well over 6s."""
        self.sock.settimeout(0.3)
        deadline = time.time() + (60 if until else seconds)
        while time.time() < deadline:
            try:
                msg = self.recv()
                self.events.append(msg)
                if until and msg.get("method") == until:
                    deadline, until = time.time() + seconds, None
            except (socket.timeout, TimeoutError):
                pass
        self.sock.settimeout(None)

    def tap(self, mode):
        x, y = json.loads(self.eval(
            "JSON.stringify((b=>[b.left+b.width/2,b.top+b.height/2])"
            f"(document.querySelector('a.nav-btn[data-mode={mode}]').getBoundingClientRect()))"
        ))
        self.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        time.sleep(0.08)
        t0 = time.perf_counter()
        self.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])  # the click fires here
        return t0


def errors(page):
    page.call("Runtime.enable")
    page.call("Log.enable")
    page.call("Network.enable")
    page.call("Page.enable")
    page.call("Page.reload")
    page.collect(4, until="Page.loadEventFired")  # plus a few polls after the load
    for e in page.events:
        method, p = e.get("method", ""), e.get("params", {})
        if method == "Runtime.exceptionThrown":
            print("EXCEPTION", json.dumps(p["exceptionDetails"])[:400])
        elif method == "Runtime.consoleAPICalled" and p["type"] in ("error", "warning"):
            print("CONSOLE", p["type"], " ".join(str(a.get("value", a.get("description"))) for a in p["args"])[:300])
        elif method == "Network.responseReceived" and p["response"]["status"] >= 400:
            print("HTTP", p["response"]["status"], p["response"]["url"])
        elif method == "Network.loadingFailed" and not p.get("canceled"):
            print("FAILED", p.get("errorText"))
    print("state:", page.eval(
        "JSON.stringify({htmx: typeof htmx, mode: (document.getElementById('panel-state')||{dataset:{}}).dataset.mode,"
        " panel: document.getElementById('panel').innerText.slice(0, 60)})"
    ))


def taps(page, modes):
    state = "(document.getElementById('panel-state')||{dataset:{}}).dataset.mode"
    lit = "(document.querySelector('a.nav-btn.active')||{dataset:{}}).dataset.mode"
    for mode in modes:
        t0 = page.tap(mode)
        page.eval(lit)
        t_lit = (time.perf_counter() - t0) * 1000
        while page.eval(state) != mode:
            if time.perf_counter() - t0 > 5:
                print(f"{mode:9} MISSED - panel still on {page.eval(state)}")
                break
            time.sleep(0.005)
        t_panel = (time.perf_counter() - t0) * 1000
        # A poll that was in flight during the tap must not put the old screen back.
        seen = set()
        end = time.perf_counter() + 1.5
        while time.perf_counter() < end:
            seen.add(page.eval(state))
            time.sleep(0.02)
        reloads = page.eval("performance.getEntriesByType('navigation')[0].type + '@' + Math.round(performance.now())")
        print(f"{mode:9} button {t_lit:4.0f}ms  panel {t_panel:5.0f}ms  modes afterwards {sorted(m for m in seen if m)}  page {reloads}")
        time.sleep(0.3)


def egg(page):
    for sequence in (["ranking", "order", "ranking", "settings", "ranking"],
                     ["ranking", "ranking", "order", "settings", "ranking"]):
        page.call("Page.enable")
        page.call("Page.navigate", url="https://localhost:8443/kiosk/")
        page.collect(1, until="Page.loadEventFired")
        for mode in sequence:
            page.tap(mode)
            time.sleep(0.7)
        time.sleep(2)
        print(" -> ".join(sequence), "=>", page.eval("location.pathname"))
    page.call("Page.navigate", url="https://localhost:8443/kiosk/")


def main():
    command, args = sys.argv[1], sys.argv[2:]
    page = Page()
    if command == "errors":
        errors(page)
    elif command == "eval":
        print(page.eval(args[0]))
    elif command == "taps":
        taps(page, args)
    elif command == "egg":
        egg(page)
    elif command == "cookie":
        cookies = page.call("Network.getCookies", urls=["https://localhost:8443/"])["cookies"]
        print(next(c["value"] for c in cookies if c["name"] == "sessionid"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
