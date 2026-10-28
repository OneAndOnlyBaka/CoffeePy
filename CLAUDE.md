# Working on CoffeePy

Notes for whoever (or whatever) edits this repo next. [README.md](README.md) covers what the
project *is* and how to deploy it; this file covers what bites you while changing it.

## Running things

No Django is installed system-wide on the dev box - use the git-ignored `.venv`, or make one
first if the checkout has none (see README's Development section). Then:

```bash
export DJANGO_TEST_SQLITE=1 DJANGO_SECRET_KEY=dev   # both are required, also for tests
python manage.py test                                # everything; ends with "Ran N tests in Ts"
python manage.py test kiosk.tests.test_thank_you     # single module
python manage.py runserver
```

`DJANGO_TEST_SQLITE=1` swaps Postgres for a local SQLite file; without it every command tries
to reach the `db` container. CI (`.github/workflows/ci.yml`) runs that suite (plus `makemigrations --check`), the same
suite against PostgreSQL with the coverage below, the audit (see "Every PR is audited"), and
a lint job of correctness checks only: ruff `F`, ShellCheck errors, zizmor on the workflows,
gitleaks over the history. No formatter, no type checker.
The suite only grows, so don't trust a count or a duration written down anywhere: the last
lines of a run say how many tests ran and how long they took, and `skipped=N` how many need
something SQLite can't give them.

`scripts/test.sh [labels]` runs the same suite in containers (`docker-compose.test.yml`) against
a real PostgreSQL and with the image's `pg_dump`/`openssl`, which is where those skipped
backup/restore tests run - and it ends with a branch-coverage report (`.coveragerc`). The
backup and update code (`coffee/backups.py`, `dashboard/restore_jobs.py`, `dashboard/views_backup.py`,
`dashboard/views_update.py`, `install/storage_helper.py`, `install/update_watcher.py`) is kept
at full line and branch coverage; read the report's Miss/BrPart columns for those files.

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
- **Everything that changes data gets an audit row** via `dashboard.audit.log(request,
  "noun.verb", …)`, right after the change succeeds - in the dashboard, the kiosk and the
  portal alike (the latter two pass the member as `actor`). Source and client address are
  filled in from the request; failures get `outcome="failure"`. Background work uses
  `audit.log_actor(...)`, the host's processes append to `data/storage/events.jsonl`. The log
  is the BSI-style record of what happened on the device - a new action that doesn't write
  one is a gap. Login locking counts failed logins *in this table* (`dashboard/security.py`),
  so a reset or a restore also clears running locks. The login rules themselves live in the
  database (`dashboard.models.PasswordPolicy`, "Login & passwords"), within `POLICY_LIMITS`;
  a password that a stricter rule outdates is caught at its owner's next login.
  The kiosk's ranking cache relies on it: `services.ranking_fingerprint()` includes the
  newest audit id, which is the only thing that moves when an old booking is corrected.
- **There is no Django admin.** Dashboard users and their roles (admin = superuser, manager)
  live in the dashboard's "Dashboard users"; corrections to orders/payments in "Bookings".
  A new page goes into `dashboard/templatetags/dashboard_nav.py` - sidebar, phone tabs and
  the "More" sheet are all drawn from that one list.
- **A member's name is "First L."** or a short code like "AB" (`coffee/names.py`: format,
  placeholders, keyboard mashing, no two active members alike, plus the admin's list in
  `dashboard.models.NamePolicy`). What the legacy database showed shaped it: a name that only
  lacks the form ("Anna B") is fixed silently at the next tap, and a bare first name is offered
  back as the start of the new one (`names.fixable`, `names.suggestion`). The
  kiosk won't take an order from a member whose name fails it - the card tap goes to the
  name screen first (`then_order` in the session) - and the dashboard's member form applies
  the same rules. So **test fixtures that order need a valid name** (`"Alice X."`), and so do
  the manual's demo members (`demo_data.py` NAMES).
- Tests live in `<app>/tests/test_*.py`. UI text is bilingual - see "Two languages" below.
- **A change users can see also changes the manual** (`docs/MANUAL.de.md` + `.en.md`, with
  screenshots in both languages) - the `user-manual` skill in `.claude/skills/` has the
  mapping and the capture scripts. Not done until both manuals match - unless the user says
  to review it first: then no screenshots and no manual edits until they approve (see the skill).

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
- **The ranking and the pillory are optional** (`dashboard.models.KioskBoards`, dashboard →
  System): personal data at a workplace, so a new device starts with both off and migration
  0005 keeps them on only where members existed. `kiosk_views()` is the ranking screen's tabs;
  empty hides the nav button (`#panel-state`'s `data-ranking`, read by `nav_state.js` on every
  poll) and the podium medal after an order. **Tests that use the ranking switch it on** in
  `setUp` - the test database starts as new as a fresh device.
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

When the status ends, the kiosk reloads - so **every container restart belongs inside the
maintenance window**. A `compose up` after it (the provisioning tool once had one) hits the
freshly reloaded page: its HTML arrives, its CSS and scripts don't, and without htmx nothing
ever reloads it. `install.sh`'s verify step recreates the containers for that reason; the
inline script in `kiosk/index.html` is the safety net.

## Backups go to one USB stick

The web container can't touch block devices, so `install/storage_helper.py` does, on the host
(`coffeepy-storage.service`), and talks to the dashboard through files in `data/storage/`:
`status.json` (what's plugged in), `requests/*.json` (format/adopt/eject), `device-id` and
`stick.json` (the one stick this device backs up to). `coffee/backups.py` is the app side.

- **Only the stick in `stick.json` is ever written.** Its metadata must carry this device's
  id *and* that stick id - an older stick of the same device is read-only. The mount point
  is a plain directory on the SD card while no stick is in; anything that writes there
  without checking the metadata first fills the card instead.
- **The stick reaches the containers through an `rslave` bind of `/srv/coffeepy-usb`**
  (`docker-compose.yml`) - a stick plugged in after the container started still shows up.
- **`pg_dump`/`pg_restore` in the image are newer than the server** (17 against 16). A 17 dump
  has `SET transaction_timeout`, which 16 rejects; `backups.restore` filters it. A restore
  is one psql transaction (drop + load): a broken backup rolls back instead of leaving an
  empty database - which is exactly what happened once with a two-step restore.
- A restore replaces the dashboard logins and sessions too - the restoring admin may be
  logged out afterwards.

## Django traps that already cost time here

- **`{# … #}` is only a comment within one line.** Django's lexer runs without `DOTALL`, so a
  multi-line one renders as literal text on the screen. Use `{% comment %}`.
- **Background threads need `TransactionTestCase`** and their own connection cleanup
  (`connections.close_all()` in a `finally`) - see `dashboard/import_jobs.py`.
- **Several gunicorn workers** (`GUNICORN_WORKERS` in `.env`, read by `docker-compose.yml`; the
  provisioning tool picks it by the Pi's RAM), so module-level state is per-process and
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
  `--dry-run` prints the whole plan and touches nothing; with `--only` it runs without root.
  Sourced, the script defines its functions and runs nothing (`coffee/tests/test_installer.py`).
  Those two are the only ways to test changes to it without a Pi.
- **Beware `set -o pipefail` in helpers**: `tr … | head -c N` fails when head closes the
  pipe, which is why `random_token` tolerates that status and then checks the length.
  Without `set -E` the ERR trap never fires inside a function, and a failure deep in a
  step exits with no explanation at all.
- The kernel's `pn533` driver grabs the ACR122U before pcscd can; `install/
  blacklist-coffeepy-nfc.conf` is what keeps the reader working. Commands to the reader
  itself (beep, LEDs - ACS's API manual, chapter 6) go through the PC/SC escape channel,
  which libccid refuses unless `ifdDriverOptions` is `0x0001` (the bridge's Dockerfile), and
  on Linux under `SCARD_CTL_CODE(1)`, not the manual's Windows 3500. The bridge turns the beep
  off that way. The red LED can't be turned off the same way: the firmware lights it while it
  polls for cards, which pcscd needs - only a bridge that polls itself (`InAutoPoll`, as V1's
  libnfc did) could, tried and not done.
- The DS3231 is read at boot by the kernel, but nothing writes it: the dashboard sets the
  clock from inside a container, and an offline box never gets the NTP sync that would
  trigger the kernel's own RTC update. `install/rtc_sync.sh` on a timer closes that loop -
  and deliberately only writes on a step of more than a minute, so the accurate RTC does
  not slowly inherit the Pi's own drift. It also reports the module's health to
  `data/storage/rtc.json` (`coffee/rtc.py`: the kiosk's red clock sign, the overview's
  "Clock module" row); "lost" - a flat battery - only clears once the RTC kept its time
  across a reboot, which is why it remembers the boot id it was lost in. A nearly flat battery
  (measured: 0.9V on a test module) is invisible to all of this: the DS3231 keeps its registers
  but its oscillator stands still without mains, and it doesn't set OSF either - it comes back
  with a valid, frozen time. Offline nothing can tell; the manual tells people what it looks
  like (the kiosk shows the time of the power cut). A clock that is
  *plausible but wrong* - the legacy device ran ten hours behind at the end - is
  `coffee/clock.py`'s job: the backup service's minutely `tick` puts it forward when it went
  back within a boot (`CLOCK_BOOTTIME`) or stands behind the newest audit entry, never back,
  and not at all once NTP set it (`"ntp"` in `rtc.json`). The container runs in UTC, so the
  clock is always set as a Unix time (`system_clock.set_system_epoch`) - a local wall-clock
  string handed to `date -s` used to set it off by the UTC offset.
- **`install/kiosk_watchdog.sh`** (a timer, every minute) restarts the kiosk session when
  `data/storage/kiosk-heartbeat` goes stale - the panel view touches it, at most every 30s per
  worker - and the stack when `/health/` stops answering. It stays out of anything with a
  maintenance status and gives up after three steps an hour, so a device a restart can't help
  isn't kept restarting.
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
  minutes, and NM's own wait-online costs ~30s on a box without uplink - `docker.service` is
  ordered after `network-online.target`, so both are disabled. The Imager's cloud-init is
  switched off too (it waits for the network on every boot), and the journal is made
  persistent: Raspberry Pi OS keeps it in RAM, so a slow or crashed boot left no trace.
  NM also runs with netplan here and, for every connection it knows, writes netplan YAML
  and runs `netplan generate` plus a daemon-reload (~4s each): the installer keeps Docker's
  interfaces away from NM and turns netplan's connections into NM keyfiles
  (`quiet_networkmanager`; the YAML goes to `/etc/netplan.coffeepy-bak`). One generate per
  boot is left - NM does that with no YAML at all. A drop-in setting NM's `unmanaged-devices`
  must use `+=`, or it silently replaces the access point's `wlan0` entry.
- **Boot time is CPU, not waiting.** `systemd-analyze blame`/`critical-chain` and the
  containers' start times say where it goes. Only the `migrate` service runs
  `manage.py prepare` (migrate, admin, TLS - one Django process; the others set
  `COFFEEPY_PREPARE=0`), because each Django start costs seconds on a Pi 3. That also took
  away an accidental safety net: after a power cut Docker restarts every container at once,
  `depends_on` or not, while PostgreSQL is still recovering - and a long-running process has
  to survive that itself. The MQTT listener keeps the reader status until the database takes
  it (`ReaderStatus`); before, a status that arrived too early was lost and the kiosk said
  "card reader not available" with a working reader. It also compares its last status with the row
  every 30s: a reset, restore or import replaces the database under it, and the bridge only
  reports a change (the reset itself leaves `NfcReaderStatus` alone - `coffee/purge.py`). Starting Chromium
  in parallel with the stack was tried and measured: both slowed down by half a minute and
  the box went into swap, so the kiosk waits for `/health/` first. Measure from the
  device's journal, not by polling it over SSH - that alone slowed a boot by 30s.
  Chromium itself waited 25s at every start for the XDG desktop portal (no desktop, so its
  GTK backend dies and the portal sits out D-Bus's timeout): the installer masks the portal
  globally - look for a pause in the journal before blaming the CPU.
- **The kiosk's night rest** (dashboard → System, `dashboard.models.NightPolicy`) is carried out
  by the kiosk session, not the app: `dashboard/night.py` writes `data/storage/night.json` (on save
  and in every `tick`, so a restore catches up), and `install/kiosk_night.py` - stdlib, the host
  has no Django - mirrors its `is_night`; keep the two identical (`test_night` compares them).
  The file carries `TIME_ZONE`, because the host's own zone may be UTC. At night the autostart
  ends Chromium and holds DPMS off; the watchdog leaves the kiosk alone then, and also a browser
  younger than its stale limit (the morning's, with the evening's heartbeat). Taps at night are
  never booked: `kiosk.views.index` starts every page load at the newest scan. Measured on the
  Pi: dark within 4s of the night's start, polling 28s after the morning's - but the first
  Chromium after a *reboot* during the night hit the network-service crash below and needed the
  90s restart.
- **The AP's channel is picked at every hostapd start** (`install/ap_channel.sh`, an
  `ExecStartPre`): as an access point the radio only hears its own channel, so the scan has
  to happen before. Transmit power needs no setting - the Pi 3 already runs at the
  regulatory maximum (20 dBm in DE); `iw` lives in `/usr/sbin`, not in a user's PATH.
- **Since trixie `docker` (the client) is the separate `docker-cli` package**, only a
  Recommends of `docker.io`, and the installer runs with `--no-install-recommends`.
- **`/tmp` is a RAM-backed tmpfs** on trixie (and Fedora) - half the RAM (`df -h /tmp` on the
  device). An update bundle with its images is bigger than that on a Pi 3 (`ls -l` the output of
  `build_update_bundle.sh`), so neither `build_update_bundle.sh` nor `update_watcher.py` may stage
  it there.
- **Updates are signed, checked, and undoable** (`install/update_watcher.py`): the bundle's
  `manifest.json` is signed with the key from `scripts/update_signing_key.sh` (public half in
  `install/update-signing.pub`, installed to `/etc/coffeepy/`), names the platform, the space
  it needs and every file's checksum. Before an update the database goes to the SD card
  (`data/updates/pre-update/`, never the backup stick) and the running images are tagged
  `:coffeepy-previous-*`; if `/health/` doesn't answer afterwards, all of it goes back. A new
  `.env` setting reaches existing devices through `env_defaults` in the manifest (taken from
  `.env.example`). The watcher runs `install.sh --only=hardware,services,kiosk` itself, with
  `COFFEEPY_FROM_WATCHER=1` - then it must not restart the watcher, not touch the
  maintenance status (it is the watcher's), not re-pin the display mode (only provisioning
  knows it) and find the kiosk user without `SUDO_USER` (root's service has none, and there is
  no `pi` on a current image). Until `--only` took a list, those steps never ran at all. A device can
  trust its own key instead (`/etc/coffeepy/update-signing.own.pub`, from `provision.sh`; no
  update replaces it) or check nothing (`UPDATE_VERIFY_SIGNATURE=false`).
- **Which code runs is `coffeepy-version.json`** (`scripts/write_version.sh`: GitVersion's
  SemVer from `GitVersion.yml` - the release workflow hands it in as `COFFEEPY_VERSION`, a local
  `gitversion` is asked, else git describe - and the commit), git-ignored and written into the tree by everything that builds or ships it -
  the image has no `.git`. The dashboard's version list (`dashboard/versions.py`) adds what
  the host's update watcher sees in `data/updates/system.json`: OS and each container's image
  id - the only thing that tells two untagged builds apart. A new way of building or
  shipping the tree has to call `write_version.sh`, or the device reports "dev". Every page
  shows it (`config.context_processors.version`: dashboard navigation, portal, the kiosk's
  admin QR codes) - constant per build, so the kiosk's wait screen stays byte-identical. A
  SemVer may carry `+metadata`, which an image tag can't: the release workflow swaps it for `-`.
- **One thing at a time:** update, restore and import check each other through
  `data/maintenance/status.json` (`restore_jobs.maintenance_active`, the watcher's
  `other_maintenance`).
- **Current Raspberry Pi OS drives HDMI through KMS**, where `hdmi_group`/`hdmi_mode`/
  `hdmi_cvt` are ignored. A panel with no usable EDID (the documented 1024x600 one) stays
  black unless the mode is forced with `video=` on the kernel command line.
- **The Pi 3's GPU has 256MB (CMA), and GPU rasterization fills it with GIF frames**:
  Chromium keeps every decoded frame of an animated GIF as a texture, the ranking's GIFs
  are 120-160 frames of ~1MB, and the GPU process then dies (`dmesg`: "Failed to allocate
  from GEM DMA helper", `gpu-recent-crash-count` in its arguments). Hence
  `--disable-gpu-rasterization` in `install/kiosk/openbox-autostart`. A new long animation
  is worth a look at `/sys/kernel/debug/dri/0/bo_stats` on the device while it plays.
- **After a power cut, Chromium's network service crashes at its first start** ("Network service
  crashed or was terminated" in `$XDG_RUNTIME_DIR/chromium.log`; three times in three tries; once
  also after a clean reboot, when the night rest had delayed the start - never after a killed
  browser), the kiosk's page load dies with it and Chromium
  stays on `about:blank` - a white screen, the app answering. The root cause is inside
  Chromium; `install/kiosk/openbox-autostart` restarts a browser whose kiosk hasn't polled
  within `KIOSK_LOAD_SECONDS`. Reproduce with `sync; echo b > /proc/sysrq-trigger`.

## Every PR is audited

CI's `audit` job fails on every known vulnerability that an upgrade fixes (Dependabot's weekly
PRs keep it there, `.github/dependabot.yml`): `pip-audit` per
requirements file (`scripts/audit_python.py` tells fixable from not-yet-fixed) and Grype on
the two images this repo builds. Other people's images (postgres, mosquitto, caddy) are only
reported - their packages are theirs to fix, and every build pulls their newest tag. What can't
be fixed yet goes into `.grype.yaml` with its reason, never silently: the two CPython CVEs there
are fixed only in 3.15, which Django 5.2 doesn't support. The images run Python 3.14 and
upgrade the base image's pip, because both lagged behind their fixes.

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
