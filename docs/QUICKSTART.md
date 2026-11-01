# Quickstart

From nothing to a running CoffeePy: on a PC to try it, on a Raspberry Pi for real, with or
without internet - and how to bring over the data of CoffeePy V1. What every screen does is
in the manual ([English](MANUAL.en.md), [German](MANUAL.de.md)); how it is built in
[ARCHITECTURE.md](ARCHITECTURE.md).

- [Try it on a PC](#try-it-on-a-pc)
- [Install it on a Raspberry Pi](#install-it-on-a-raspberry-pi)
- [Installing without internet](#installing-without-internet)
- [Coming from V1](#coming-from-v1)
- [Development](#development)

## Try it on a PC

Docker (or Podman) with Compose v2:

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

Without a card reader nothing can be tapped - the kiosk waits on "apply your card". For QR
codes a phone can open, set `PUBLIC_HOST` in `.env` to your PC's LAN address (see
[ARCHITECTURE.md](ARCHITECTURE.md#reachable-address-for-qr-codes)). Rootless Podman can't bind
port 443 by default - allow it with `sysctl net.ipv4.ip_unprivileged_port_start=443`.

## Install it on a Raspberry Pi

Start from a plain **Raspberry Pi OS Lite (64-bit)**, flashed with Raspberry Pi Imager - what
the releases are built for and the device was measured on (the manual's
[What the device needs](MANUAL.en.md#what-the-device-needs)); 32-bit works too, with bundles
built for `linux/arm/v7`. Set a user and, in "Edit Settings", enable SSH. The Pi needs
internet for the install, until the very last step: a **network cable** is the better choice -
that last step can turn the Pi's WiFi into CoffeePy's own access point, and a connection over
its previous WiFi then drops. Enter your WiFi in the Imager only if there is no cable.

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

The manual walks through every screen of the tool and every setting
([Installation](MANUAL.en.md#2-installation)), and then through the
[first steps](MANUAL.en.md#3-first-steps): the certificate, the dashboard login, coffee sorts
and members.

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
keeps the kernel's own NFC driver off the reader, the storage helper for the backup stick, the
systemd units, and finally - after asking - the standalone WiFi access point. It copies the app to
`/opt/coffeepy` and generates `/opt/coffeepy/.env` with per-device secrets. Reboot when it
is done.

Worth knowing:

- **It is resumable and safe to re-run.** Every step is recorded in
  `/var/lib/coffeepy/install-state`; a re-run skips what already succeeded (`--force`
  repeats them anyway). Interrupted downloads, a flaky mirror or a closed SSH session
  cost you nothing but the time.
- **`--dry-run` prints the whole plan without touching anything** (as root - without root only
  together with `--only`).
  Worth doing once before the real run.
- **`--skip-ap` leaves networking alone** if the Pi should stay on your existing network
  instead of hosting its own.
- Files it changes outside `/opt/coffeepy` are backed up once with a `.coffeepy-bak`
  suffix, and everything is logged to `/var/log/coffeepy-install.log`.
- The display mode is pinned for the documented 1024x600 panel. Current Raspberry Pi OS
  drives HDMI through KMS, where the classic `hdmi_group`/`hdmi_mode`/`hdmi_cvt` settings
  are ignored and a panel with no usable EDID simply stays black. Override with
  `VIDEO_MODE=` (empty) or your own `video=` string.

**Devices installed before HTTPS** need two host-side pieces an update bundle cannot
deliver - the kiosk session now opens `https://localhost:8443` and imports the device CA
with `certutil`. Run `sudo ./install/install.sh --only=packages` and `--only=kiosk` once
from a current checkout; with the update alone the kiosk keeps pointing at the old,
now closed port 8000.

## Installing without internet

The install above needs the network until its last step. For a machine that will never
have one - or to install the identical set of bits on several devices - build a bundle on
a dev machine first:

```bash
./scripts/build_offline_bundle.sh                 # linux/arm64 by default
./scripts/build_offline_bundle.sh linux/arm/v7    # for a 32-bit Pi OS
```

It collects the `.deb` packages from the *real* Raspberry Pi OS archives (Raspbian plus
archive.raspberrypi.com, resolved in a container of the target architecture - Debian's own
archive does not have `chromium` or `rpd-plym-splash` under those names), Docker's Compose
plugin, the app images built for the target platform, and this checkout. Building the
images cross-compiles under qemu, so the machine needs working binfmt emulation - the same
prerequisite any ARM cross-build has.

On the device:

```bash
tar xf coffeepy-offline-<date>.tar -C ~
sudo ~/coffeepy-offline/repo/install/install.sh --offline=$HOME/coffeepy-offline
```

`--offline` swaps out exactly the three steps that would otherwise reach for the network:
packages come from a `file://` apt archive inside the bundle (apt still resolves the
dependency order - the bundle ships a `Packages` index for that), the Compose plugin is
copied in after its checksum is verified, and the app images are `docker load`ed instead
of built. Everything else is identical, including the hardware and kiosk configuration.

The bundle carries the complete recursive dependency closure rather than only what a
solver thinks is missing, so it does not matter what the collection container had
installed. Last counted (for a 32-bit target) that was 784 packages, about 500MB, plus the
app images - expect roughly 1.5GB in total, which is a USB stick rather than an email. In
exchange it is bigger than an online install's download, and it is tied to one OS release -
build it for the release your device runs (`RELEASE=trixie` by default).

The package list and the pinned Compose version live in [install/packages.txt](../install/packages.txt)
and [install/versions.env](../install/versions.env), read by both the installer and the
bundle builder - so a bundle cannot contain something other than what the installer
expects.

Updates later on go the same way - as a file, uploaded in the dashboard:
[ARCHITECTURE.md](ARCHITECTURE.md#offline-updates).

## Coming from V1

The V1 sources (`CoffeePy.py`, the Tkinter `gui/`, `lib/`, the Flask `web/` dashboard and
the `setup_*.sh` scripts) are gone from the working tree - V2 replaces all of them and
nothing in it depends on them. They remain in the git history if you need to look
something up. What carried over unchanged: the 3D-printable housing
([ARCHITECTURE.md](ARCHITECTURE.md#housing)) and your data. There is no in-place upgrade path
from a running V1 device: install V2 fresh and import the database.

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

The manual's [Moving over from version 1](MANUAL.en.md#moving-over-from-version-1) covers the
same from the dashboard, including what happens to names that don't fit today's rules.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DJANGO_TEST_SQLITE=1 DJANGO_SECRET_KEY=dev   # sqlite instead of Postgres; both are required
python manage.py migrate
python manage.py test
python manage.py runserver
```

[CLAUDE.md](../CLAUDE.md) collects the conventions and the non-obvious traps of this codebase
(the kiosk's 500ms poll, the legacy SQLite quirks, why the Pi can't load anything remote) -
worth reading before the first change, whether you're a person or an agent. Tests against
PostgreSQL, the audit, the lint and the release builds: [CI.md](CI.md).
