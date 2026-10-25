"""HTTP load against the Pi from this machine, stdlib only.

    python3 -I load.py kiosk     the kiosk's own poll, 2/s for 60s
    python3 -I load.py mixed     kiosk poll + 5 phones on the portal + 2 dashboard users
    python3 -I load.py burst     20 clients polling flat out next to the kiosk

Requests go to the Pi's LAN address but carry Host: <PUBLIC_HOST> - Django only accepts
localhost and PUBLIC_HOST (the AP address on an installed device), and a mismatch is a
400 that looks exactly like a fast response. Every row reports its errors; read them.
"""
import http.cookiejar
import os
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
BASE = f"http://{ENV['PI_HOST']}:8000"
HOST = os.environ.get("PUBLIC_HOST", "192.168.4.1:8000")


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def hit(opener, path, errors):
    t = time.perf_counter()
    try:
        response = opener.open(urllib.request.Request(BASE + path, headers={"Host": HOST}), timeout=30)
        response.read()
        ok = response.status == 200
    except Exception as e:  # noqa: BLE001 - every failure is a data point here
        ok = False
        errors.add(f"{path}: {e}")
    return (time.perf_counter() - t) * 1000, ok


def worker(path, n, interval, out, errors):
    opener = client()
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
    run(*SCENARIOS[sys.argv[1]])
