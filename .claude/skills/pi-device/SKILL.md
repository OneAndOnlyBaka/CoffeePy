---
name: pi-device
description: Install, deploy to, inspect and measure CoffeePy on the user's test Raspberry Pi (touchscreen kiosk, NFC reader, RTC, WiFi AP). Use whenever a change has to be tried on the real device, the user reports what the Pi's screen shows, or asks for installer, update-bundle, performance or WiFi tests there.
---

# CoffeePy on the test Pi

A Raspberry Pi 3B with the documented hardware: 1024x600 HDMI touchscreen, ACR122U reader,
DS3231 RTC. It is a **test device**: the user re-flashes it when something breaks, so
installing, rebooting and restarting services there needs no confirmation. A real
deployment (any other host) does.

Everything below goes through `scripts/` in this skill directory; run them by path from
the repo root, e.g. `.claude/skills/pi-device/scripts/pi.sh uptime`.

## Setup (once per flash)

Connection data is in `.device/pi.env` at the repo root - `PI_HOST`, `PI_USER`,
`PI_PASSWORD`. It is excluded via `.git/info/exclude`: never commit it, never print the
password, never copy it anywhere else. If the file is missing, ask the user for the
values and create it (mode 600).

`pi.sh --setup` installs this machine's SSH key and passwordless sudo; after that every
script uses key auth. A re-flashed Pi changes its host key - `--setup` clears the old one.

The image so far was Raspberry Pi OS **Desktop** 64-bit (trixie) on ethernet, not the Lite
docs/QUICKSTART.md documents - `install.sh` disables the desktop's lightdm itself.

## Install and deploy

| Want | Do |
|---|---|
| Fresh install | `sync.sh`, then `pi.sh` runs `cd CoffeePy && sudo nohup ./install/install.sh --yes > ~/install.out 2>&1 &` - the image build takes ~10 min, watch `~/install.out` with Monitor instead of a blocking call |
| Installer change | `sync.sh`, then `install.sh --only=STEP --yes` for the affected step(s) - or `deploy.sh STEP...` |
| App code, templates, CSS/JS | `deploy.sh` (they are baked into the image - a restart is not enough) |
| Kiosk session files (`install/kiosk/`) | `deploy.sh kiosk` |
| Anything touching boot config, RTC, display mode | the step, then `pi.sh 'sudo systemctl reboot'`, wait for SSH, re-check |

`install.sh --dry-run` runs anywhere and shows the whole plan - do it before a real run
after changing the installer. The installer's own log is `/var/log/coffeepy-install.log`.

## Reaching the app

Only Caddy is reachable (see CLAUDE.md, "Everything goes through Caddy"): port 443 from the
network, `127.0.0.1:8443` - the only place `/kiosk/` answers - from the Pi itself. From
here, `curl --resolve coffeepy.local:443:$PI_HOST --cacert <copy of data/tls/ca.crt>
https://coffeepy.local/...` checks the real certificate chain; on the Pi, `curl --cacert
/opt/coffeepy/data/tls/ca.crt https://localhost:8443/...`. Port 8000 is closed on purpose.

## Looking at the device

- **What is on the screen:** `screenshot.sh OUT.png` (into the scratchpad), then Read the
  PNG. It prints the mean brightness: `1.0` blank white, `0` black, `~0.81` the kiosk.
- **The kiosk browser itself:** `kiosk-browser.sh on`, then `errors` (reloads the page,
  shows console errors and failed requests - the first thing to run when the screen looks
  wrong), `eval 'JS'`, `taps order info ...` (tap latency with real touch events, and
  whether a stale poll flips the screen back), `cookie`.
  **Always `kiosk-browser.sh off` afterwards.** on/off restart the X session.
- **App errors:** `pi.sh 'cd /opt/coffeepy && sudo docker compose logs --since 10m web'` -
  500s are logged (`config/settings.py` LOGGING). A 500 only one browser gets usually
  lives in *that* session: `kiosk-browser.sh cookie`, then replay with a test `Client`
  carrying that `sessionid` and `raise_request_exception=True` via `django.sh`.
- **Startup:** `startup-timeline.sh` restarts the session and logs black/white/kiosk every 3s.
- **Both languages:** the device's default is `DJANGO_LANGUAGE_CODE` in `/opt/coffeepy/.env`;
  the kiosk's DE/EN button (or a `django_language` cookie set over CDP) switches the kiosk.
  Check a changed screen in both - a German string can overflow where the English one fit.
- **The maintenance screen:** `pi.sh 'sudo bash -c ". /opt/coffeepy/install/maintenance.sh;
  maintenance_begin update; maintenance_step images 35"'` shows it, removing
  `/opt/coffeepy/data/maintenance/status.json` ends it (the kiosk reloads itself).
- **Screenshots for the manual** are the `user-manual` skill's job (its capture.sh) - they
  are scripted so they can be retaken; don't hand-place a screenshot from here into docs/.

## Measuring

Measure before and after every optimisation, on the device - the dev machine says nothing
about a Pi 3. Report numbers, not impressions.

- `resources.sh [SECONDS]` - CPU and memory per process group, temperature, throttling.
  (`docker stats` shows 0B: the Pi kernel disables the memory cgroup.)
- `python3 -I scripts/load.py kiosk|mixed|burst` - HTTP latency/throughput from here. Portal
  and dashboard go through Caddy on 443 with `Host: 192.168.4.1` (PUBLIC_HOST); a wrong host is a
  fast **400** - check the errors column before believing a number. The kiosk only exists on the
  Pi's 127.0.0.1:8443, so its requests go through an SSH tunnel (a 404 there means they didn't).
  Not while the Pi builds images - the numbers then measure the build.
- `django.sh perf_data.py seed` → `django.sh page_timings.py` → `django.sh perf_data.py
  cleanup` - every page's server time against five years of synthetic history. An empty
  database makes every page look fast; the kiosk rows are the ones that matter (polled
  every 500ms).
- Chromium tap latency: `kiosk-browser.sh taps ...` (see above).
- **When the kiosk is usable:** delete `/opt/coffeepy/data/storage/kiosk-heartbeat`, reboot (or
  restart `getty@tty1`), then `stat -c %W` of it is the first poll - its birth time, which ext4
  keeps although the file is touched every 30s. Costs the device nothing while it boots.
- **Without ethernet** the Pi is reached over its own WiFi at 192.168.4.1 (`PI_HOST` in
  `.device/pi.env`; the host key is the same). A reboot drops this machine off the WiFi, and it
  may not rejoin by itself.

## Things that already went wrong here

- **Never create `NfcScan` rows on the device.** The table is global and the live kiosk
  polls it: a synthetic scan logged the real touchscreen in as a test member, and deleting
  that member afterwards left the kiosk on "Loading…" with a 500 on every poll.
  `perf_data.py`/`page_timings.py` log their own client in through its session instead.
  If you need a real tap, ask the user to tap a card.
- **`/tmp` is a ~450MB RAM tmpfs** on trixie. Nothing large (update bundles, image saves)
  goes there - use the home directory or `/opt/coffeepy/data/`.
- **Python on the Pi host has no third-party modules** - hence the stdlib-only websocket
  client in `cdp.py`. In the web container, Django is available via `django.sh`.
- **Background installs:** start them with `nohup … &` on the Pi, so a dropped SSH session
  does not kill them, and follow the log with Monitor.
- **The screen is black, then white, for ~45s** after every session restart or boot,
  even with the app already up - that is Chromium starting, not a hang. Wait before
  judging a screenshot; `startup-timeline.sh` shows the phases.
- **A CDP expression that ends in a DOM node returns nothing.** `Runtime.evaluate` with
  `returnByValue` can't serialise an element, so `a && document.querySelector(...)` is never
  "true" to a polling script - wrap conditions in `!!(...)`. Cost two capture runs.
- **Provisioning changes go through `scripts/provision.sh`** where possible (interactive in
  tmux, or `--answers ... --unattended` with `PI_PASSWORD` from `.device/pi.env`) - that tests
  the tool along with the change. It re-runs every installer step (`--force`), ~20 minutes.
- **Session-restarting commands** (`kiosk-browser.sh on/off`, `deploy.sh kiosk`,
  `startup-timeline.sh`) interrupt whoever is using the touchscreen - say so if the user
  might be at the device.

## Update bundles

`scripts/build_update_bundle.sh --unsigned --output ~/bundle.tar` can run **on the Pi itself**
(native arm64, Docker present) - this machine has neither Docker nor arm64 emulation. It
re-tags `coffeepy-web:latest` on the Pi, though: re-tag the running image back
(`docker tag <running sha> coffeepy-web:latest`) or the update has nothing to change.
Upload through `/dashboard/update/` (login + CSRF with curl and a cookie jar, Host header
as above), or drop the file into `/opt/coffeepy/data/updates/incoming/` directly; results
land in `data/updates/status/*.log`. Test a broken bundle followed by a good one as well.
The bundle built there is unsigned and the device refuses it: **never copy the private signing
key to the Pi** - fetch only `manifest.json`, sign it here (`openssl pkeyutl -sign -rawin -inkey
~/.config/coffeepy/update-signing.key -in manifest.json -out manifest.sig`) and put `manifest.sig`
back into the tar.

## Cleaning up

Before finishing: `kiosk-browser.sh off` if it was on, `perf_data.py cleanup` if seeded,
remove test bundles and markers, and leave the kiosk on its normal screen (screenshot).
