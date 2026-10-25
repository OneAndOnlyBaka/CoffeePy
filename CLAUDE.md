# Working on CoffeePy

Notes for whoever (or whatever) edits this repo next. [README.md](README.md) covers what the
project *is* and how to deploy it; this file covers what bites you while changing it.

## Running things

No Django is installed system-wide on the dev box and there is no `.venv` in the tree - make
one before anything else (see README's Development section). Then:

```bash
export DJANGO_TEST_SQLITE=1 DJANGO_SECRET_KEY=dev   # both are required, also for tests
python manage.py test                                # ~250 tests, ~35s
python manage.py test kiosk.tests.test_thank_you     # single module
python manage.py runserver
```

`DJANGO_TEST_SQLITE=1` swaps Postgres for a local SQLite file; without it every command tries
to reach the `db` container. CI (`.github/workflows/ci.yml`) runs exactly `python manage.py
test` with those two variables and nothing else - no linter, no formatter, no type checker.

## The four apps

| App | Who looks at it | Auth |
|---|---|---|
| `coffee/` | nobody - models, `services.py` (all ORM queries), management commands | - |
| `kiosk/` | the touchscreen next to the machine | NFC card tap, session only |
| `dashboard/` | admins, on a phone or PC | Django auth, `@superuser_required` for the destructive parts |
| `portal/` | members, on their own phone | `@portal_login_required` (rolling 6-month session from a one-time link QR) |

Rules that hold across all four:

- **Queries live in `coffee/services.py`**, not in views. It's ported from the legacy app's
  `DatabaseConnector` and is where anything aggregate belongs.
- **"Cups" means `Order.machine_strokes`**, not `Order` rows - a jug is two cups. Every
  ranking, statistic and comparison counts strokes. Getting this wrong is invisible in tests
  with one-stroke fixtures.
- **Dashboard mutations get an audit row** via `dashboard.audit.log(request, "noun.verb", …)`,
  right after the change succeeds. Background work uses `audit.log_actor(username, …)`.
- Tests live in `<app>/tests/test_*.py`. UI text is bilingual - see "Two languages" below.
- **A change users can see also changes the manual** (`docs/MANUAL.de.md` + `.en.md`, with
  screenshots in both languages) - the `user-manual` skill in `.claude/skills/` has the
  mapping and the capture scripts. Not done until both manuals match.

## The kiosk polls, and that breaks things

`#panel` is re-fetched every ~500ms (`static/kiosk/panel_scroll.js`) so a card tap shows up
immediately. Consequences, all of which have already caused bugs:

- **A swapped-in `<img>` restarts its animated gif at frame 0.** So does `hx-preserve`, which
  moves the live node and thereby detaches/re-attaches it. `panel_stable_swap.js` skips the
  swap entirely when a poll returns byte-identical HTML - that, and only that, is what lets
  the "thank you" gifs play through. Do not put anything varying per render (a countdown, a
  csrf token) into a screen whose animation has to survive - which is why the "apply your
  card" screens' clock is filled in by `static/kiosk/clock.js` instead of `{% now %}`.
  `test_wait_screen_is_identical_from_poll_to_poll` guards that. Their GIFs come from
  `scripts/make_wait_gifs.py`.
- **Screens with a form always differ per poll anyway**: Django re-masks `{% csrf_token %}` on
  every render. Never key anything on "the panel HTML changed".
- **The QR modal lives outside `#panel`** so the poll can't wipe it mid-scan - so nothing
  removes it either. `panel.html` renders `data-panel-key="<state>:<uid>"` and
  `panel_modal.js` closes the modal when that changes (auto-logout, next member's card).
- **Tapping a nav button logs the session out** (`views.nav` → `_reset_session_state`). To
  reach a logged-in Settings screen in a test: choose the mode first, *then* create the
  `NfcScan`. `NfcScan.uid_as_int()` is little-endian - `"AABBCCDD"` is member `0xDDCCBBAA`.

## Everything goes through Caddy

Only `caddy` publishes ports (`docker-compose.yml`): **443** for the network and
**127.0.0.1:8443** for the kiosk's own browser. `web:8000` is reachable from the compose
network only - `curl localhost:8000` on the device finds nothing.

- **`/kiosk/` exists only on 8443.** Caddy answers it with a 404 on 443, which is the whole
  of the "kiosk only from the device" guarantee - there is no check in Django. Links meant
  for phones come from `coffee.network.public_base_url`, which drops the request's port
  behind Caddy: a QR code carrying `:8443` would point at a port no phone can reach.
- **Certificates and the Caddyfile are written by `dashboard/tls.py`**, never by hand
  (`data/tls/`, mounted as `/tls`). Caddy runs with `--watch` but only reloads when the
  *parsed* config changes, so the files it loads are named after their own hash
  (`live-net-<hash>.crt`) - a new certificate under an old path would simply be ignored.
- **`data/tls/` holds the device CA that phones have installed.** `install/src-filter.txt`
  keeps the installer's rsync from deleting it; losing it means every phone has to trust a
  new one. The CA is created once; `ensure_tls` (migrate, and the backup loop) only
  re-issues the leaf certificates.
- On the device, talk to the app as `curl --cacert /opt/coffeepy/data/tls/ca.crt
  https://localhost:8443/...`. Chromium trusts the CA through the kiosk user's NSS store,
  filled by `install/kiosk/openbox-autostart` with `certutil` on every session start.

## Two languages

Every screen exists in German and English (`settings.LANGUAGES`). Which one a device shows is
its own cookie - the kiosk's DE/EN button, the switch in dashboard and portal - or else the
default chosen at setup (`DJANGO_LANGUAGE_CODE`, asked by `scripts/provision.sh`), never the
browser's language (`config/language.py`).

- **msgids are the English text.** Templates use `{% translate %}`/`{% blocktranslate trimmed
  with x=... %}`, Python `gettext`/`gettext_lazy`, never f-strings inside `_()`. A word with two
  meanings gets a context (`pgettext`): "Order" is *Bestellen* on the kiosk but *Reihenfolge*
  in the payment-method form.
- **After changing any marked string:** `manage.py makemessages -l de --no-location
  --ignore=.venv/* --ignore=static/* --ignore=staticfiles/* --ignore=nfc-cardreader/*
  --ignore=scripts/* --ignore=.claude/*`, translate what is new **and every entry it marks
  fuzzy** - its guesses are wrong ("Restarting" became "Start") - then `compilemessages -l de
  --ignore=.venv`. The .mo is committed; `coffee/tests/test_translations.py` fails if it lags
  the .po or anything is untranslated. German says "du", like the English copy's tone.
- **German formats numbers with a comma.** Anything a program reads back - `<input value>`,
  `data-*`, inline `<script>`, CSS widths - needs `|unlocalize` / `{% localize off %}`
  (`json.dumps` output is safe). The kiosk's pay view accepts "1,50" for that reason.
- **Text that is stored is stored in English** and translated where it is shown: the thank-you
  message lives in the session, the alt-card error in the database (read by kiosk, dashboard
  and portal, each in its own language). "Cash" is data too - show it via `|method_label`.
- The kiosk's language button is a GET link, not `set_language`'s form: a csrf token would
  make the wait screen differ per poll (see above).

## Maintenance screen

`install.sh` and the update watcher write their progress to `data/maintenance/status.json`
(`install/maintenance.sh`); Caddy serves it itself, so the kiosk's `maintenance.js` can read it
while the app is down, and covers the screen with "We'll be right back" until it is over. Caddy
answers 502s with `data/tls/maintenance.html` (both languages, rendered by `dashboard/tls.py`).
A new installer step or watcher phase needs a label in `kiosk.views.MAINTENANCE_STEPS`; the
installer's rsync keeps `data/maintenance/` (`src-filter.txt`) or it would delete the status
mid-run.

## Django traps that already cost time here

- **`{# … #}` is only a comment within one line.** Django's lexer runs without `DOTALL`, so a
  multi-line one renders as literal text on the screen. Use `{% comment %}`.
- **Background threads need `TransactionTestCase`** and their own connection cleanup
  (`connections.close_all()` in a `finally`) - see `dashboard/import_jobs.py`.
- **Three gunicorn workers** (`docker-compose.yml`), so module-level state is per-process and
  useless for anything the browser polls. The legacy-DB import writes its progress to a JSON
  file (`settings.IMPORT_STATUS_FILE`) for that reason - and also because the import runs in
  one big transaction, so progress written to the *database* would only appear once it ends.

## Legacy data

V1's sources (Tkinter GUI, Flask dashboard, `lib/`) were removed from this repo when V2
landed - they are in the git history only. What still matters is its SQLite database,
because `import_sqlite` reads it. That schema is not obvious and it is the source of a
whole class of bugs:

- `coffee_order` has **no coffee-sort column**. The sort only exists inside the free-text
  `order_info` as `"Ordered ID [<rowid>]"`, where the rowid indexes `coffee_sorts`. Drop it
  and the statistics page's distribution and price-trend charts go empty.
- Today's kiosk writes `"Ordered [<pk>] <name>"` - a *Django* pk. The `Ordered ID` prefix is
  what tells legacy rows from current ones (`backfill_order_sorts` relies on it).
- `import_sqlite` is **not idempotent**: coffee sorts and members are matched by name/uid, but
  orders and payments are inserted unconditionally. Re-importing duplicates history. Use
  `--purge` (or the dashboard checkbox) to start clean, or `backfill_order_sorts` to repair
  existing rows without re-importing.
- Legacy timestamps are local wall-clock strings; `_parse_datetime` makes them aware in
  `TIME_ZONE`. Statistics that group by hour or weekday must use `ExtractHour` /
  `ExtractIsoWeekDay`, which convert to local time - grouping raw UTC is off by an hour.

## Provisioning tool

`scripts/provision.sh` is how a Pi is normally set up and reconfigured: it asks for everything
that goes into `/opt/coffeepy/.env` and runs `install.sh` over SSH. **It has to follow every
option the app or the installer gains** - otherwise the new option exists, but nobody can set it:

- a new `.env` setting → `build_env` (and `load_existing`, so a re-run keeps it), an entry in
  the settings menu (`step_settings` + `edit_setting`), `ANSWER_KEYS` + the header comment, the
  settings table in both manuals, and `.env.example`;
- a new `install.sh` flag or step → `step_install` (flags), `step_percent`/`step_label`, the
  installer's `step_weight`, and a kiosk label in `MAINTENANCE_STEPS`;
- every text through `t "English" "Deutsch"` (German with "du"), validators included.

Test it for real: interactively in tmux (`tmux send-keys`, `capture-pane`) against the test Pi,
and `--answers FILE --unattended` for the scripted path. It must stay sourceable - the manual's
screenshot tooling draws single screens with its functions.

## Installing on the device

Trying something on the real test Pi (deploy, screenshots, the kiosk's browser, load and
page timings, update bundles) goes through the `pi-device` skill in `.claude/skills/`.

`install/install.sh` turns a plain Raspberry Pi OS Lite into the kiosk. It replaced both a
two-phase setup script and a pi-gen image build; what those cost us is worth keeping in
mind before changing it:

- **Package names have to be checked against Raspberry Pi OS, not Debian.** No `chromium`
  in Raspbian (it comes from the Raspberry Pi archive), no `docker-compose-v2` anywhere
  (hence the pinned, checksummed plugin binary), and the "pix" boot splash is
  `rpd-plym-splash`. The installer therefore verifies every package *before* installing
  any of them - a wrong name half-way through leaves a half-configured device.
- **The AP step comes last and asks first.** It takes the Pi off the network the earlier
  steps need, including the SSH session it is probably running in.
- **Steps are recorded in `/var/lib/coffeepy/install-state`** so a re-run resumes.
  `--dry-run` prints the whole plan and touches nothing - it is also the only way to test
  changes to this script without a Pi.
- **Beware `set -o pipefail` in helpers**: `tr … | head -c N` fails when head closes the
  pipe, which is why `random_token` tolerates that status and then checks the length.
  Without `set -E` the ERR trap never fires inside a function, and a failure deep in a
  step exits with no explanation at all.
- The kernel's `pn533` driver grabs the ACR122U before pcscd can; `install/
  blacklist-coffeepy-nfc.conf` is what keeps the reader working.
- The DS3231 is read at boot by the kernel, but nothing writes it: the dashboard sets the
  clock from inside a container, and an offline box never gets the NTP sync that would
  trigger the kernel's own RTC update. `install/rtc_sync.sh` on a timer closes that loop -
  and deliberately only writes on a step of more than a minute, so the accurate RTC does
  not slowly inherit the Pi's own drift.
- **The Raspberry Pi archive keys are SHA-1 signed**, and every modern verifier refuses
  them by default: debootstrap picks sqv over gpgv when only sqv is installed, and apt on
  trixie uses sqv outright - both then report the archives as unsigned. The fix is a
  Sequoia policy file re-enabling SHA-1 (`SEQUOIA_CRYPTO_POLICY`), not `[trusted=yes]`;
  the signatures are still worth checking.
- **The offline bundle and the installer share their inputs on purpose**:
  `install/packages.txt` and `install/versions.env` are read by both, so a bundle can
  never carry a different Compose version or package set than the installer expects.
  `scripts/build_offline_bundle.sh` resolves the packages inside a container of the
  *target* architecture with the Raspberry Pi archives configured - the only place those
  names resolve the way they will on the device.
- **NetworkManager owns WiFi on bookworm/trixie**, also on Lite. For the AP, wlan0 has to be
  unmanaged *and* the radio switched on through `nmcli` - NM keeps its own "WiFi off" state
  (the default when the Imager set up no WiFi) and re-applies the rfkill block every boot.
  Enabling systemd-networkd also enables its wait-online, which then stalls boot by two
  minutes; `coffeepy.service` is ordered after `network-online.target`.
- **Since trixie `docker` (the client) is the separate `docker-cli` package**, only a
  Recommends of `docker.io`, and the installer runs with `--no-install-recommends`.
- **`/tmp` is a RAM-backed tmpfs** on trixie (and Fedora) - ~450MB on a Pi 3. An update
  bundle is ~700MB, so neither `build_update_bundle.sh` nor `update_watcher.sh` may stage
  it there.
- **`update_watcher.sh` processes bundles inside a `while` fed by `inotifywait`**, i.e. in a
  subshell: an `exit` there ends the loop while the service still reports active. Failure
  paths `return`. The dashboard upload itself holds a gunicorn sync worker for minutes,
  hence `--timeout` in `docker-compose.yml`.
- **Current Raspberry Pi OS drives HDMI through KMS**, where `hdmi_group`/`hdmi_mode`/
  `hdmi_cvt` are ignored. A panel with no usable EDID (the documented 1024x600 one) stays
  black unless the mode is forced with `video=` on the kernel command line.

## The device is offline

The Pi runs without internet, in a browser it never leaves. **Nothing may load from an
external host** - no CDN, no Google Fonts, no image hotlinked from anywhere. htmx and Chart.js
are vendored under `static/vendor/`; new images belong in `static/`.

Templates and static files are baked into the image at build time and `collectstatic` runs
there (`Dockerfile`), so **template or CSS/JS changes need a rebuild**, not a restart.

## Style

Comments explain *why*, not what - the existing ones are worth reading before adding more, and
matching their density matters more than adding your own. Lines run to ~120 characters. No
formatter is configured, so match the surrounding file rather than any tool's opinion.
