"""Records the headless Chromium (CDP port 9333) running the demo copy of CoffeePy.

Screencast frames arrive only when something changes; each keeps its timestamp, and ffmpeg's
concat demuxer turns them into a constant 30 fps clip. Taps are real mouse events at an
element's centre, with a ripple drawn on top so the viewer sees where the finger went.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import PY, REPO, WORK  # noqa: E402

sys.path.insert(0, str(REPO / ".claude" / "skills" / "pi-device" / "scripts"))
os.environ["CDP_PORT"] = "9333"
import cdp  # noqa: E402

S = str(WORK)
BASE = "http://127.0.0.1:8765"
APP = f"{S}/app"
PY = str(PY)
ENV = dict(os.environ, DJANGO_TEST_SQLITE="1", DJANGO_SECRET_KEY="dev", DJANGO_DEBUG="1")

RIPPLE = """(() => { if (window.__tap) return; window.__tap = (x, y) => {
  const d = document.createElement('div');
  d.style.cssText = `position:fixed;left:${x-28}px;top:${y-28}px;width:56px;height:56px;border-radius:50%;`
    + 'background:rgba(255,255,255,.55);border:3px solid rgba(40,40,40,.35);z-index:2147483647;pointer-events:none;'
    + 'transform:scale(.4);opacity:1;transition:transform .45s ease-out,opacity .45s ease-out';
  document.documentElement.appendChild(d);
  requestAnimationFrame(() => requestAnimationFrame(() => { d.style.transform='scale(1.25)'; d.style.opacity='0'; }));
  setTimeout(() => d.remove(), 700); }; })()"""


def django(code):
    return subprocess.run([PY, "manage.py", "shell", "-c", code], cwd=APP, env=ENV,
                          capture_output=True, text=True).stdout.strip().splitlines()[-1:]


def scan(who):
    subprocess.run([PY, "manage.py", "shell"], cwd=APP, env=dict(ENV, SCRIPT_ARGS=f"scan {who}"),
                   stdin=open(f"{APP}/.claude/skills/user-manual/scripts/demo_data.py"), capture_output=True)


class Browser:
    def __init__(self):
        self.page = cdp.Page()
        for domain in ("Page", "Network", "Runtime"):
            self.page.call(f"{domain}.enable")
        self.page.call("Network.setCookie", name="django_language", value="en", url=BASE + "/")
        # The clock reads 08:42 today, not the small hours this was recorded in: only a bare new Date()
        # is shifted (clock.js), Date.now() stays true for the countdowns' server deadlines.
        now = time.localtime()
        shift = ((8 * 60 + 42) * 60 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)) * 1000
        self.page.call("Page.addScriptToEvaluateOnNewDocument", source=f"""(() => {{ const D = Date;
          class S extends D {{ constructor(...a) {{ if (a.length) super(...a); else super(D.now() + {shift}); }} }}
          S.now = D.now; S.parse = D.parse; S.UTC = D.UTC; window.Date = S; }})()""")

    def viewport(self, width, height, scale=1, mobile=False):
        self.page.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                       deviceScaleFactor=scale, mobile=mobile)
        if mobile:
            self.page.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)

    def go(self, path):
        self.page.call("Page.navigate", url=BASE + path)
        self.page.collect(0.8, until="Page.loadEventFired")
        self.js(RIPPLE)

    def js(self, expression):
        return self.page.eval(expression)

    def wait(self, expression, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"!!({expression})") is True:
                return
            time.sleep(0.1)
        raise RuntimeError(f"timed out waiting for: {expression}")

    def state(self):
        return self.js("(document.getElementById('panel-state')||{dataset:{}}).dataset.panelKey || ''")

    def wait_state(self, prefix):
        self.wait(f"((document.getElementById('panel-state')||{{dataset:{{}}}}).dataset.panelKey||'').startsWith({json.dumps(prefix)})")

    def tap(self, selector, pause=0.25):
        box = self.js(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                      f" return [r.left + r.width/2, r.top + r.height/2]; }})()")
        x, y = box
        self.js(RIPPLE)
        self.js(f"window.__tap({x}, {y})")
        time.sleep(pause)
        for kind in ("mousePressed", "mouseReleased"):
            self.page.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def scroll(self, dy, steps=30, duration=1.2):
        for _ in range(steps):
            self.js(f"window.scrollBy(0, {dy / steps})")
            time.sleep(duration / steps)


class Recorder:
    """Its own DevTools connection, so frames are acked while the main one waits on calls."""

    def __init__(self, name, max_size=(2048, 2048)):
        self.name = name
        self.dir = f"{S}/clips/{name}"
        os.makedirs(self.dir, exist_ok=True)
        for f in os.listdir(self.dir):
            os.remove(f"{self.dir}/{f}")
        self.conn = cdp.Page()
        self.conn.sock.settimeout(0.2)
        self.frames = []
        self.running = True
        self.conn._send({"id": 1, "method": "Page.startScreencast",
                         "params": {"format": "png", "maxWidth": max_size[0], "maxHeight": max_size[1]}})
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while self.running:
            try:
                msg = self.conn.recv()
            except (socket.timeout, TimeoutError):
                continue
            if msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                path = f"{self.dir}/{len(self.frames):05d}.png"
                with open(path, "wb") as f:
                    f.write(base64.b64decode(p["data"]))
                self.frames.append((p["metadata"]["timestamp"], path))
                self.conn._send({"id": 2, "method": "Page.screencastFrameAck", "params": {"sessionId": p["sessionId"]}})

    def stop(self, hold=0.0):
        time.sleep(hold)
        end = time.time()
        self.running = False
        self.thread.join()
        self.conn.sock.settimeout(None)
        self.conn._send({"id": 3, "method": "Page.stopScreencast", "params": {}})
        lines = []
        for i, (ts, path) in enumerate(self.frames):
            nxt = self.frames[i + 1][0] if i + 1 < len(self.frames) else end
            lines += [f"file '{path}'", f"duration {max(nxt - ts, 0.001):.4f}"]
        lines.append(f"file '{self.frames[-1][1]}'")
        with open(f"{self.dir}/list.txt", "w") as f:
            f.write("\n".join(lines) + "\n")
        out = f"{S}/clips/{self.name}.mkv"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{self.dir}/list.txt",
                        "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "ffv1",
                        "-pix_fmt", "rgb24", out], check=True)
        print(f"{out}: {len(self.frames)} frames, {self.frames[-1][0] - self.frames[0][0] + 0:.1f}s")
        return out
