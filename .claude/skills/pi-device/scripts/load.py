"""HTTP load against the Pi from this machine, stdlib only.

    python3 -I load.py kiosk     the kiosk's own poll, 2/s for 60s
    python3 -I load.py mixed     kiosk poll + 5 phones on the portal + 2 dashboard users
    python3 -I load.py burst     20 clients polling flat out next to the kiosk

Portal and dashboard requests go to Caddy on the Pi's LAN address (port 443) but carry
Host: <PUBLIC_HOST> - Django only accepts its own names (the AP address on an installed
device), and a mismatch is a 400 that looks exactly like a fast response. The certificate is
not checked: it is issued for the AP address, not the LAN one this connects to.
/kiosk/ only exists on the Pi's 127.0.0.1:8443 (Caddy answers it with a 404 on 443), so the
kiosk's requests go through an SSH tunnel to there, as Host: localhost - the path the
kiosk's own Chromium takes, plus the tunnel's few ms. Every row reports its errors; read them.
"""
import http.cookiejar
import os
import socket
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "--show-toplevel"],
                      capture_output=True, text=True, check=True).stdout.strip()
ENV = dict(line.strip().split("=", 1) for line in open(os.path.join(ROOT, ".device", "pi.env"))
           if "=" in line and not line.startswith("#"))
BASE = f"https://{ENV['PI_HOST']}"
HOST = os.environ.get("PUBLIC_HOST", "192.168.4.1")
KIOSK_BASE = None  # https://127.0.0.1:<the tunnel's port>, once open_tunnel() ran
INSECURE = ssl._create_unverified_context()


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                       urllib.request.HTTPSHandler(context=INSECURE))


def open_tunnel():
    """ssh -L to the Pi's loopback kiosk port; returns the ssh process."""
    global KIOSK_BASE
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tunnel = subprocess.Popen(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                               "-L", f"127.0.0.1:{port}:127.0.0.1:8443", f"{ENV['PI_USER']}@{ENV['PI_HOST']}"])
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            if tunnel.poll() is not None:
                sys.exit("ssh tunnel to the kiosk port failed")
            time.sleep(0.1)
    KIOSK_BASE = f"https://127.0.0.1:{port}"
    return tunnel


def hit(opener, path, errors):
    kiosk = path.startswith("/kiosk/")
    url, host = (KIOSK_BASE, "localhost") if kiosk else (BASE, HOST)
    t = time.perf_counter()
    try:
        response = opener.open(urllib.request.Request(url + path, headers={"Host": host}), timeout=30)
        response.read()
        ok = response.status == 200
    except Exception as e:  # noqa: BLE001 - every failure is a data point here
        ok = False
        errors.add(f"{path}: {e}")
    return (time.perf_counter() - t) * 1000, ok


def worker(path, n, interval, out, errors):
    opener = client()
    if path.startswith("/kiosk/"):
        hit(opener, "/kiosk/", errors)  # a session, like the real kiosk has
    for _ in range(n):
        t0 = time.perf_counter()
        out.append(hit(opener, path, errors))
        if interval:
            time.sleep(max(0, interval - (time.perf_counter() - t0)))


def run(label, specs):
    results, threads, errors = {}, [], set()
    for name, path, users, n, interval in specs:
        results[name] = []
        for _ in range(users):
            t = threading.Thread(target=worker, args=(path, n, interval, results[name], errors))
            t.start()
            threads.append(t)
    t0 = time.perf_counter()
    for t in threads:
        t.join()
    duration = time.perf_counter() - t0
    print(f"== {label} ({duration:.0f}s)")
    for name, rows in results.items():
        ms = sorted(r[0] for r in rows)
        failed = sum(not r[1] for r in rows)
        print(f"   {name:16} n={len(ms):4} median={statistics.median(ms):5.0f}ms p95={ms[int(len(ms) * .95) - 1]:5.0f}ms "
              f"max={ms[-1]:5.0f}ms errors={failed} rps={len(ms) / duration:.1f}")
    for e in sorted(errors)[:5]:
        print("   !", e)


SCENARIOS = {
    "kiosk": ("kiosk poll alone (2/s)", [("kiosk panel", "/kiosk/panel/", 1, 120, 0.5)]),
    "mixed": ("kiosk + 5 phones + 2 admins", [
        ("kiosk panel", "/kiosk/panel/", 1, 120, 0.5),
        ("portal", "/portal/", 5, 30, 2.0),
        ("dashboard login", "/dashboard/login/", 2, 30, 2.0)]),
    "burst": ("20 clients flat out", [
        ("kiosk panel", "/kiosk/panel/", 1, 60, 0.5),
        ("burst panel", "/kiosk/panel/", 20, 25, 0)]),
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SCENARIOS:
        sys.exit(__doc__)
    tunnel = open_tunnel()
    try:
        run(*SCENARIOS[sys.argv[1]])
    finally:
        tunnel.terminate()
