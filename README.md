![](docs/pictures/coffeepy.png)

# CoffeePy V2 (Django + Postgres + NFC-over-MQTT)

Software to handle coffee matters: a self-service tally for a shared coffee machine that
replaces pen and paper, with NFC cards for identification.

This is **V2**, a full rework as a single Django project: Postgres instead of SQLite, the
Tkinter GUI and Flask dashboard replaced by a touchscreen kiosk and admin dashboard that
both run as web apps (HTMX), and NFC card reads delivered through the
[`nfc-cardreader`](nfc-cardreader/) ACR122U→MQTT bridge. Everything ships via
`docker compose`, including on a Raspberry Pi that runs fully offline.

> **Disclaimer – this is not a point-of-sale system.** CoffeePy replaces the paper tally sheet
> next to a shared coffee machine, nothing more. It does not sell anything and is not a cash
> register: no receipts, no tax or VAT handling, no tamper-proof fiscal records (no TSE as the
> German KassenSichV requires, no GoBD-compliant bookkeeping), and it doesn't process payments –
> a payment entered on the kiosk or the phone is a note that someone says they paid, and the
> payment links only open the member's own app. Balances are an informal tally among the people
> sharing the machine. If coffee is sold commercially, that needs a real POS system. CoffeePy
> is provided as is, without warranty – see [LICENSE](LICENSE).

**New here?** [docs/MANUAL.md](docs/MANUAL.md) covers what hardware to buy and how to use
the kiosk/dashboard day to day. This README is the technical/installation reference.

### Coming from V1?

The V1 sources (`CoffeePy.py`, the Tkinter `gui/`, `lib/`, the Flask `web/` dashboard and
the `setup_*.sh` scripts) are gone from the working tree - V2 replaces all of them and
nothing in it depends on them. They remain in the git history if you need to look
something up. What carried over unchanged: the [3D-printable housing](#housing) and your
data - see [Migrating existing data](#migrating-existing-data) for
importing an old `coffee.db`. There is no in-place upgrade path from a running V1 device:
install V2 fresh and import the database.

## Architecture

| Service        | What it does                                                            |
|----------------|--------------------------------------------------------------------------|
| `db`           | Postgres                                                                  |
| `mosquitto`    | MQTT broker                                                               |
| `nfc-bridge`   | Existing ACR122U→MQTT bridge (unmodified, see [nfc-cardreader/](nfc-cardreader/)) |
| `migrate`      | One-shot `manage.py migrate` + `ensure_tls`, gates startup of the services below |
| `web`          | Django (gunicorn): kiosk UI (`/kiosk/`) + admin dashboard (`/dashboard/`) |
| `caddy`        | The only way in: HTTPS on 443, and the kiosk on 127.0.0.1:8443 (see [HTTPS](#https-and-certificates)) |
| `mqtt-listener`| Subscribes to the bridge's MQTT topic, writes `NfcScan` rows              |
| `backup`       | Periodic `pg_dump` to a (USB-stick-backed) directory                     |

The kiosk (`/kiosk/`) is an HTMX page that polls every 500ms for new `NfcScan` rows -
no websockets/Channels/Redis needed. The dashboard (`/dashboard/`) uses Django's built-in
auth with two roles - admins (superusers: everything, including the system pages and the
dashboard's own user management) and managers (members, bookings, coffee sorts, payment
methods). Single orders and payments are corrected under "Bookings", each change with an
audit-log entry. There is no Django admin (`/admin/` doesn't exist).

## Quick start

```bash
cp .env.example .env   # edit passwords/secret key
docker compose build
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Then open `https://localhost:8443/kiosk/` (touchscreen UI - only reachable from the
machine itself) and `https://localhost/dashboard/` (admin dashboard).
The certificate comes from a CA the stack creates on first start, so the browser warns
until you install `data/tls/ca.crt` (or download it from `/certificate/`).

### Migrating existing data

If you have an old CoffeePy `coffee.db` (or one of its `coffeeN.db` backups - which are
actually SQL dump text, not a SQLite binary - `import_sqlite` handles both):

```bash
docker compose cp /path/to/coffee.db web:/tmp/coffee.db
docker compose exec web python manage.py import_sqlite --path /tmp/coffee.db
```

By default the import merges into whatever is already there (matching by name/UID). Add
`--purge` to delete existing members, cards, orders, payments and coffee sorts first - in
the same transaction, so a failed import leaves the current data untouched. The dashboard
offers the same as a checkbox at `/dashboard/import/`.

If an earlier version of this command was used, its orders have no coffee sort (the legacy
schema records it only inside the order's info text) and the statistics page shows an empty
distribution chart and a price trend with no lines. Repair those rows in place - do not
re-import, that would duplicate every order:

```bash
docker compose exec web python manage.py backfill_order_sorts --path /tmp/coffee.db --dry-run
docker compose exec web python manage.py backfill_order_sorts --path /tmp/coffee.db
```

### Backups

Backups go to a USB stick that an admin sets up under `/dashboard/backup/`: the host service
`coffeepy-storage` (`install/storage_helper.py`) formats it (ext4, label `COFFEEBAK`), marks it
with this device's id and remembers it. From then on the `backup` service writes the whole
device to it at the interval set on that page - database (`pg_dump --format=custom`), device
CA and certificates, settings -, optionally encrypted with `BACKUP_PASSPHRASE`, read back and
checksummed, and thinned out by generations (newest, daily, weekly, monthly). Pulled out,
backups pause (the dashboard's overview and the kiosk say so) and resume when the same stick
comes back. The same page restores a backup - all of it or parts, from the stick, another
device's stick, or an uploaded file, older `.sql` dumps included - and downloads one without
a stick.

### Offline updates

The Pi is meant to run without internet, so updates are pushed manually instead of pulled:

1. On a dev machine with internet: `./scripts/update_builder.sh` (guided) or
   `./scripts/build_update_bundle.sh [--platform linux/arm64]` builds and signs a bundle of
   every image `docker-compose.yml` defines for the Pi's architecture - `coffeepy-web`
   (shared by `migrate`/`web`/`mqtt-listener`/`backup`) and `nfc-bridge` (its own
   Dockerfile under [nfc-cardreader/](nfc-cardreader/)) - plus pulls `db`/`mosquitto`,
   and bundles them together with `docker-compose.yml` itself into one `.tar`. The
   compose file is the only source of truth for what's in the bundle *and* what runs on
   the Pi, so the two can't quietly fall out of sync - and shipping it is how you add a
   whole new container (a new service block), not just update existing ones.
2. Carry it over (USB stick, or a laptop joined to the Pi's own WiFi AP) and upload it at
   `/dashboard/update/` (superuser only).
3. A host-level systemd service (`coffeepy-update-watcher.service`,
   `install/update_watcher.py`) checks the signature (against `install/update-signing.pub`,
   or a device's own key - see the manual's "Your own signing key"), every file's checksum,
   platform, space and version; saves the database and the running images to the SD card;
   `docker load`s the images, drops in the new `docker-compose.yml` and runs
   `docker compose up -d --remove-orphans`. If `/health/` doesn't answer afterwards, it
   rolls everything back by itself. The web container itself never gets Docker/host access.

### System time

`/dashboard/system-time/` (superuser only) is the offline fallback if the RTC drifts or
isn't fitted - normally `install/install.sh` enables the DS3231 RTC overlay so NTP
isn't needed at all.

### HTTPS and certificates

Nothing reaches the app except through Caddy: port 443 for the network, and
`127.0.0.1:8443` for the kiosk's own browser - the one place `/kiosk/` is served, so the
touchscreen UI can't be opened from anywhere but the device itself. Port 80 and the app's
own port 8000 are not published at all.

- **On first start** the stack creates its own CA (10 years) and signs the network
  certificate with it, for `PUBLIC_HOST`, `192.168.4.1` and `coffeepy.local`
  (`TLS_EXTRA_HOSTNAMES` in `.env`). Everything lives in `data/tls/` on the host; the
  installer never touches it on a re-run. Certificates are renewed automatically before
  they expire, and re-issued when the names change - the CA stays, so phones keep trusting it.
- **Phones and PCs** install the CA once from `/certificate/` - linked, without a login,
  on the dashboard and portal pages - and the browser warning is gone for good. On an
  iPhone it then also has to be enabled under *Settings › General › About › Certificate
  Trust Settings*.
- **Your own certificate** (PEM, chain included, plus its unencrypted key) can be uploaded
  at `/dashboard/certificate/` (superuser only) and replaces the network certificate within
  seconds; a button goes back to the self-signed one. The kiosk keeps the device's own
  certificate regardless, so a bad upload can never lock the touchscreen out.

`dashboard/tls.py` explains the moving parts. Rootless Podman can't bind port 443 by
default - allow it with `sysctl net.ipv4.ip_unprivileged_port_start=443`.

**Devices installed before HTTPS** need two host-side pieces an update bundle cannot
deliver - the kiosk session now opens `https://localhost:8443` and imports the device CA
with `certutil`. Run `sudo ./install/install.sh --only=packages` and `--only=kiosk` once
from a current checkout; with the update alone the kiosk keeps pointing at the old,
now closed port 8000.

### Reachable address for QR codes, and the System dashboard

Anything meant to be opened from a *different* device (the dashboard/link-your-phone QR
codes on the kiosk) needs an address that device can actually reach - not "localhost",
which is what the kiosk's own browser sees since it and the app run on the same machine.
`PUBLIC_HOST` in `.env` controls this, and is automatically added to
`DJANGO_ALLOWED_HOSTS` so Django doesn't reject requests through it. `install/
install.sh` sets it automatically for the Pi's own WiFi AP (`192.168.4.1`) - that's
the one deployment this was built to get right by default. Everywhere else, autodetection
runs *inside* the `web` container, so it normally reports the container's own
bridge-network IP rather than the host's real LAN IP (a phone can't reach that) - set
`PUBLIC_HOST` explicitly to your host's LAN IP if you want working QR codes outside the
Pi's AP setup.

`/dashboard/system/` (superuser only) shows that address alongside live CPU, RAM and disk
usage (via `psutil`) - handy for keeping an eye on a resource-constrained Pi.

### CI/CD

Two GitHub Actions workflows, both under [.github/workflows/](.github/workflows/):

- **`ci.yml`** - runs the Django test suite (`DJANGO_TEST_SQLITE=1 manage.py test`) on
  every push and pull request. No Docker/Postgres involved, just `pip install` + the
  test runner, so it's fast.
- **`release.yml`** - runs on pushing a tag matching `v*.*.*` (or manually via "Run
  workflow" for a test build without publishing a release), and builds everything an
  offline Pi deployment needs:
  - `images-and-bundle`: builds `coffeepy-web` and `nfc-bridge` for `linux/arm/v7` -
    matching a device running the 32-bit Raspberry Pi OS, which is the right choice on a
    1GB Pi 3B (`linux/arm64` is available in the manual-run dropdown for a 64-bit
    device) - pushes each to
    `ghcr.io/<owner>/coffeepy-web` and `ghcr.io/<owner>/coffeepy-nfc-bridge`
    (`:latest` and `:<tag>`), then runs `scripts/build_update_bundle.sh` and uploads the
    resulting offline update bundle.

  On a tag push the job attaches its output to a GitHub Release for that tag; otherwise
  (manual run) it is only available as a workflow-run artifact. GHCR packages are private
  by default on first publish even for a public repo - flip visibility to public under the
  repo's Packages tab if you want `docker pull` to work without logging in.

## Raspberry Pi install

Start from a plain **Raspberry Pi OS Lite** (32-bit is the right choice on a 1GB Pi 3B -
see docs/MANUAL.md), flashed with Raspberry Pi Imager. Set a user and, in "Edit Settings",
enable SSH and your WiFi so the Pi has internet for the install; it only needs that until
the very last step.

### From your Linux PC (recommended)

```bash
./scripts/provision.sh
```

A full-screen guide in the kiosk's colours: it asks for the Pi's address and SSH
credentials, checks the Pi, lets you set everything that goes into `/opt/coffeepy/.env`
(admin account, WiFi access point, the address for phones, time zone, backups, display
mode, web workers, internet or offline bundle), copies this checkout over and runs the
installer below on the Pi - showing its progress, and surviving a dropped connection. At
the end it reboots the Pi, checks HTTPS against the device's own CA and can save the
passwords to `~/coffeepy-<address>.txt` (readable only by you). Needs nothing but bash,
ssh and tar here; nothing on your PC is changed.

Run it again to change settings on an installed Pi: it starts from the existing `.env`
and keeps the database password and secret key. For several devices, `--answers FILE`
pre-fills the answers (`KEY=VALUE`, see the top of the script) and `--unattended` asks
nothing; `PI_SSH_OPTS` passes extra options to ssh (`-p 2222`, `-i key`).

### On the Pi itself

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/<owner>/CoffeePy.git
cd CoffeePy
sudo ./install/install.sh
```

That is the whole thing. It installs Docker plus the Compose v2 plugin, the kiosk stack
(X, Openbox, Chromium, autologin on tty1, the CoffeePy boot splash), the DS3231 RTC
overlay and its write-back timer, the ACR122U udev rule and the modprobe blacklist that
keeps the kernel's own NFC driver off the reader, the USB-backup-stick rule, the systemd
units, and finally - after asking - the standalone WiFi access point. It copies the app to
`/opt/coffeepy` and generates `/opt/coffeepy/.env` with per-device secrets. Reboot when it
is done.

Worth knowing:

- **It is resumable and safe to re-run.** Every step is recorded in
  `/var/lib/coffeepy/install-state`; a re-run skips what already succeeded (`--force`
  repeats them anyway). Interrupted downloads, a flaky mirror or a closed SSH session
  cost you nothing but the time.
- **`--dry-run` prints the whole plan without touching anything**, from any machine.
  Worth doing once before the real run.
- **`--skip-ap` leaves networking alone** if the Pi should stay on your existing network
  instead of hosting its own.
- Files it changes outside `/opt/coffeepy` are backed up once with a `.coffeepy-bak`
  suffix, and everything is logged to `/var/log/coffeepy-install.log`.
- The display mode is pinned for the documented 1024x600 panel. Current Raspberry Pi OS
  drives HDMI through KMS, where the classic `hdmi_group`/`hdmi_mode`/`hdmi_cvt` settings
  are ignored and a panel with no usable EDID simply stays black. Override with
  `VIDEO_MODE=` (empty) or your own `video=` string.

### Installing without internet

The install above needs the network until its last step. For a machine that will never
have one - or to install the identical set of bits on several devices - build a bundle on
a dev machine first:

```bash
./scripts/build_offline_bundle.sh                 # linux/arm/v7 by default
./scripts/build_offline_bundle.sh linux/arm64     # for a 64-bit Pi OS
```

It collects the `.deb` packages from the *real* Raspberry Pi OS archives (Raspbian plus
archive.raspberrypi.com, resolved in a container of the target architecture - Debian's own
archive does not have `chromium` or `rpd-plym-splash` under those names), Docker's Compose
plugin, the app images built for the target platform, and this checkout. Building the
images cross-compiles under qemu, so the machine needs working binfmt emulation - the same
prerequisite any ARM cross-build has.

On the device:

```bash
tar xf coffeepy-offline-<date>.tar -C /tmp
sudo /tmp/coffeepy-offline/repo/install/install.sh --offline=/tmp/coffeepy-offline
```

`--offline` swaps out exactly the three steps that would otherwise reach for the network:
packages come from a `file://` apt archive inside the bundle (apt still resolves the
dependency order - the bundle ships a `Packages` index for that), the Compose plugin is
copied in after its checksum is verified, and the app images are `docker load`ed instead
of built. Everything else is identical, including the hardware and kiosk configuration.

The bundle carries the complete recursive dependency closure rather than only what a
solver thinks is missing, so it does not matter what the collection container had
installed. For a 32-bit target that is currently 784 packages, about 500MB, plus the app
images - expect roughly 1.5GB in total, which is a USB stick rather than an email. In exchange it is bigger than an online install's download, and it is tied to
one OS release - build it for the release your device runs (`RELEASE=trixie` by default).

The package list and the pinned Compose version live in [install/packages.txt](install/packages.txt)
and [install/versions.env](install/versions.env), read by both the installer and the
bundle builder - so a bundle cannot contain something other than what the installer
expects.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DJANGO_TEST_SQLITE=1   # use sqlite locally instead of Postgres
python manage.py migrate
python manage.py test
python manage.py runserver
```

[CLAUDE.md](CLAUDE.md) collects the conventions and the non-obvious traps of this codebase
(the kiosk's 500ms poll, the legacy SQLite quirks, why the Pi can't load anything remote) -
worth reading before the first change, whether you're a person or an agent.

## Housing

![](docs/pictures/housing.jpg)

[housing/](housing/) holds the desk housing carried over from V1 - it fits V2 unchanged,
since the hardware did not change:

- `housing/3D/` - STL files to print. `LCD_*` are the screen frame variants (`_H`
  horizontal, `_V` vertical, `_V_T1`/`_V_T2` the two-part vertical version), `Card_*` the
  card-reader holder in the matching orientations.
- `housing/labels/` - printable labels: the front badge, the screen frame and the type
  plate.

## Project layout

- `coffee/` - models (`CoffeeSort`, `Member`, `Order`, `Payment`, `NfcScan`), the ORM query
  layer (`services.py`), and management commands (`mqtt_listener`, `import_sqlite`,
  `backup_db`).
- `kiosk/` - the touchscreen order/settings/pay/ranking UI.
- `dashboard/` - admin auth, statistics, pillory, exports, backup/update/system-time.
- `portal/` - the member-facing phone view (balance, pay, own stats, cards, devices),
  reached by scanning the kiosk's "Link this phone" QR code.
- `install/` - `install.sh` (the whole device setup), systemd units, udev rules,
  hostapd/dnsmasq templates, kiosk session files, update watcher.
- `scripts/` - `build_update_bundle.sh` (dev-machine side of the offline update pipeline)
  and `build_offline_bundle.sh` (full no-internet install bundle).
- `nfc-cardreader/` - ACR122U→MQTT bridge, unchanged from V1.
- `docs/` - [MANUAL.md](docs/MANUAL.md): hardware shopping list + end-user/operator guide;
  `docs/pictures/` the images this README shows.
- `housing/` - STL files and labels for the desk housing (see above).

## Credits

The kiosk's nav icons (`static/kiosk/img/nav/`) and its "apply your card" animations
(`static/kiosk/img/wait/`) are drawn by `scripts/make_nav_icons.py` and
`scripts/make_wait_gifs.py` - change them there and re-run. V1's photographic nav icons
from [iconarchive.com](https://iconarchive.com/) were replaced to match the flat design.
