# Security mechanisms

What protects a CoffeePy device, its data and its users - by layer, with where each mechanism
lives. For what this means under data protection law see [PRIVACY.md](PRIVACY.md); for the
risks they address and the Cyber Resilience Act, [CRA.md](CRA.md); for the system as a whole,
[ARCHITECTURE.md](ARCHITECTURE.md).

CoffeePy keeps an informal tally among colleagues. It is not a payment system and moves no
money (see the README's disclaimer), so the threat it is built against is the office one: a
curious or mischievous person on the same network, someone at the touchscreen who isn't who
their card says, a lost phone, a lost backup stick, a broken update, a power cut.

- [Network](#network)
- [The kiosk](#the-kiosk)
- [The phone portal](#the-phone-portal)
- [The dashboard](#the-dashboard)
- [Web application](#web-application)
- [Audit log](#audit-log)
- [Backups](#backups)
- [Updates](#updates)
- [The device](#the-device)
- [Development and supply chain](#development-and-supply-chain)
- [Known limits](#known-limits)

## Network

- **One way in.** Only Caddy publishes ports: 443 for the network, and 8443 on `127.0.0.1`
  only, for the kiosk's own browser. The app server (8000), PostgreSQL, MQTT and port 80 are
  not reachable from outside the device (`docker-compose.yml`).
- **HTTPS everywhere**, with a device CA created on the device itself (10 years) and leaf
  certificates renewed automatically; phones install the CA once. An admin can upload their
  own certificate; the kiosk keeps the device's own regardless, so a bad upload can't lock it
  out (`dashboard/tls.py`).
- **Secure cookies.** Session, CSRF and language cookies are `Secure` on an installed device
  (`DJANGO_SECURE_COOKIES=true`, set by the provisioning tool) and session cookies `HttpOnly`.
- **Host check.** Django only answers the device's own names (`ALLOWED_HOSTS` from
  `PUBLIC_HOST` and the certificate names).
- **Offline.** Nothing is loaded from an external host - no CDN, no fonts, no analytics; the
  device needs no internet and calls nobody.
- **Optional own WiFi.** The Pi can run its own WPA2 access point for the phones, so the
  device need not join the company network at all.

## The kiosk

- **Only at the device.** `/kiosk/` exists only on `127.0.0.1:8443`; Caddy answers it with a
  404 on the network port. Nobody can drive the kiosk - or request a phone link for someone
  else - from a phone or PC.
- **The card is the identity.** A tap logs the member in for one interaction; a nav button or
  20 seconds without a touch log them out. Taps are only accepted from the reader through the
  MQTT bridge inside the device.
- **No admin functions at the touchscreen.** The wrench only shows QR codes to join the
  device's WiFi and to open the dashboard - which then asks for a login.

## The phone portal

The portal has no passwords. A phone is linked by a one-time link from the kiosk:

- only while the member is logged in at the kiosk with their card;
- a token of 256 bits from a cryptographic random generator - not guessable;
- valid for five minutes and deleted the moment it is opened; a new link replaces an unused
  older one;
- **rate-limited**: 10 invalid or expired links from one address within 15 minutes lock that
  address out for 15 minutes (counted in the audit log, so it holds across all workers);
- the phone then gets its own session - `Secure`, `HttpOnly` - valid for six months and
  extended on use;
- every linked phone is listed and can be logged out by the member (portal → Devices), by an
  admin (Revoke devices) and automatically when the member is marked as departed.

What a portal session can do is deliberately small: see that member's data, note a payment
(not move money), start linking a card - which still has to be held to the kiosk. It can't
reach the dashboard or the kiosk. The manual explains this to users in
[How linking works - and why it is safe](MANUAL.en.md#how-linking-works--and-why-it-is-safe).

## The dashboard

- **Django authentication** with PBKDF2-hashed passwords; no Django admin, no default
  password - the first admin's password is generated per device at installation (or set in
  the provisioning tool).
- **Two roles.** Admins (superusers) for everything including system pages, backups, updates,
  certificates and users; managers for members, bookings, coffee sorts and payment methods.
  The system's actions - backup and restore, update, reset, import, certificates, clock,
  dashboard users - are `@superuser_required`; a manager can't reach them.
- **Password rules after the BSI's recommendation**, set in the dashboard ("Login &
  passwords"): either long and varied enough, or a long passphrase; common and all-digit
  passwords are refused, and so are passwords too similar to the user name
  (`dashboard/security.py`). A password a stricter rule outdates must be changed at its
  owner's next login.
- **Lockout** after a configurable number of failed logins, for a configurable time - counted
  in the audit log, so it holds across workers and restarts.
- **Idle logout** after a configurable number of minutes without a request.
- An admin can require a user to change their password at the next login.

## Web application

- **CSRF protection** on every form (Django's middleware); state changes only by POST.
- **Templates auto-escape** all output; queries go through the ORM - no SQL built from strings.
- **Uploads** (update bundles, backups, certificates) are only accepted from admins and
  checked before use - signature and checksums for bundles, format and checksum for backups.
- **The web container has no host access**: no Docker socket, no block devices. Updates and
  the backup stick are handled by small host services that read requests from files and
  validate them (`install/update_watcher.py`, `install/storage_helper.py`).

## Audit log

Every change - in the dashboard, at the kiosk, in the portal, by a background job or the
host's services - writes an audit entry: when, who, what, from which address, success or
failure. Failed logins and invalid portal links are entries too, which is what the lockouts
count. Admins read it in the dashboard; entries older than `AUDIT_RETENTION_DAYS` (90 by
default) are deleted.

## Backups

- Written only to the **one stick** this device set up (its metadata carries the device and
  the stick id), never to a stick that merely happens to be plugged in.
- **Optional encryption**: with `BACKUP_PASSPHRASE` every backup is AES-256 encrypted with a
  key derived by PBKDF2 (600,000 iterations, salted); the passphrase is passed to `openssl`
  through the environment, never on a command line. Without the passphrase a backup can't be
  restored.
- Every backup is **read back and checksummed** (`.sha256`) right after it is written.
- A **restore saves the current state first** and replaces the database in one transaction: a
  broken backup rolls back instead of leaving an empty database.

## Updates

- **Signed bundles.** Each update bundle's manifest is signed with an Ed25519 key; the device
  checks the signature against the project's key or the device's own
  (`/etc/coffeepy/update-signing.own.pub`), then every file's checksum, the platform and the
  free space. The release workflow never sees a private key - bundles from CI are signed by
  whoever installs them ([CI.md](CI.md#signed-bundles---the-path-for-a-device-in-use)).
  Switching the check off is possible for development and is shown as a warning on the
  update page for as long as it is off.
- **Forward only.** An older bundle is refused - no downgrade to a version with known holes.
  The one exception is a pre-release going back to its release.
- **Self-healing.** The database is saved before every update; if the new version doesn't
  answer, or the power fails mid-update, the device returns to the version before it by itself.

## The device

- **Automatic recovery**: a watchdog restarts the kiosk session or the stack when they stop
  working (at most three times an hour); an interrupted update is repaired at the next boot.
- **Per-device secrets**: database password, Django secret key and admin password are
  generated at installation and stored in `/opt/coffeepy/.env` (readable by root only);
  the provisioning tool can save them to a file readable only by you.
- **Clock integrity**: the DS3231 keeps time offline; a clock that jumps back is put forward
  again, so audit entries and lockouts keep their order.
- **SSH and the operating system** are Raspberry Pi OS's own - CoffeePy doesn't open or harden
  them. Use a strong password or a key for the Pi's user, and keep the Pi physically in its
  housing.

## Development and supply chain

- **Every push is audited**: `pip-audit` on the Python requirements and Grype on the images
  fail the build on any known vulnerability that has a fix; what can't be fixed yet is listed
  with its reason (`.grype.yaml`).
- **Dependabot** proposes updates weekly; **gitleaks** scans the whole history for secrets;
  **zizmor** checks the GitHub workflows themselves.
- **SBOMs** (CycloneDX) of every image come with each release, so a device's software can be
  checked against new vulnerabilities without the device.
- Backup, restore and update code is kept at 100 % line and branch coverage, tested on
  PostgreSQL as well as SQLite. Details: [CI.md](CI.md).

## Known limits

- **A card is a bearer token.** Whoever holds a member's card can order on their account at
  the kiosk. MIFARE uids can be copied; the balance is an informal tally, and every booking is
  in the audit log and can be corrected.
- **A QR code can be photographed.** Whoever scans a member's link QR code first, within its
  five minutes, gets that member's portal. The member notices (their own scan fails), sees the
  extra device and can log it out.
- **Unencrypted backups are readable** by whoever has the stick. Set a passphrase if the stick
  might leave the building.
- **An admin sees everything** - hand out the admin role sparingly.

Found a security problem? Please open an issue without details, or contact the maintainer
first, so devices in use can be updated before it is public.
