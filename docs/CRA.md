# Cyber Resilience Act - risk assessment

The cybersecurity risk assessment for CoffeePy that the EU Cyber Resilience Act (Regulation
(EU) 2024/2847, "CRA") asks of a product with digital elements (Art. 13(2)-(3)), with the
essential requirements of its Annex I set against what the software does. It builds on
[SECURITY.md](SECURITY.md) (the mechanisms) and [ARCHITECTURE.md](ARCHITECTURE.md) (the
system); the data protection side is [PRIVACY.md](PRIVACY.md).

It describes the software as of this version; it is **not legal advice**. Assessed
2026-10-30; it is reviewed with every change listed under [Review](#review).

- [Does the CRA apply?](#does-the-cra-apply)
- [The product](#the-product)
- [Classification](#classification)
- [What is protected, against whom](#what-is-protected-against-whom)
- [Method](#method)
- [Risk register](#risk-register)
- [Annex I part I - security requirements](#annex-i-part-i---security-requirements)
- [Annex I part II - vulnerability handling](#annex-i-part-ii---vulnerability-handling)
- [Open actions](#open-actions)
- [Review](#review)

## Does the CRA apply?

| Who | Role under the CRA | Duties |
|---|---|---|
| This repository | Free and open-source software, MIT licence, not monetised: **outside the CRA** while it is not made available in the course of a commercial activity (Art. 3(48), recitals 18-19). | none - this assessment is voluntary, so that the next two rows can build on it |
| Someone who sells or otherwise commercially supplies a ready-built device (Pi, reader, display, the `housing/` case, CoffeePy installed) | **Manufacturer** of a product with digital elements (Art. 3(13)) - CoffeePy and Raspberry Pi OS become *components* they must exercise due diligence on (Art. 13(5)) | everything below, conformity assessment, technical documentation (Annex VII), EU declaration of conformity, CE marking, reporting (Art. 14), support period (Art. 13(8)) |
| Someone who builds one for their own office | User, not a manufacturer - nothing is placed on the market | none under the CRA; GDPR still applies ([PRIVACY.md](PRIVACY.md)) |

Dates: the reporting duty for actively exploited vulnerabilities and severe incidents
(Art. 14) applies since **11 September 2026**; everything else from **11 December 2027**.

## The product

**Intended purpose:** a touchscreen kiosk next to a shared coffee machine that replaces the
paper tally sheet - members tap an NFC card to book a coffee, see their balance and note a
payment; admins manage the device in a web dashboard; members see their own account on their
phone. The device runs offline, inside one office or group.

**Reasonably foreseeable misuse**, considered below:

- use as a point-of-sale or payment system (the README's disclaimer: it is neither - no money
  moves, a payment is a note);
- operation on an untrusted or public network instead of an office LAN or its own WiFi;
- the dashboard or port 443 published to the internet;
- the signature check switched off on a device in use (`UPDATE_VERIFY_SIGNATURE=false`);
- the Pi's SSH left on with the Imager's password.

**Parts with digital elements:** Raspberry Pi 3 or newer with Raspberry Pi OS, an ACR122U NFC
reader, a DS3231 clock module, a touchscreen; on it five containers (Caddy, the Django app,
PostgreSQL 16, Mosquitto, the NFC bridge) and host services (update watcher and recovery,
storage helper, watchdog, RTC sync, the kiosk session with Chromium). Interfaces: HTTPS on
443 (network) and `127.0.0.1:8443` (kiosk), optionally a WPA2 access point, SSH (the
operating system's), USB (reader, backup stick).

## Classification

Not listed in Annex III (important products) or Annex IV (critical products): it is no
identity management system, browser, password manager, network device or operating system
of its own. The access point serves the device's own pages only - no NAT or forwarding to
another network is configured (`install/dnsmasq.conf.template`) - so it is not a router in
the sense of Annex III class I. CoffeePy therefore falls into the **default category**:
conformity by internal control (Module A, Art. 32(1)(a)) - the manufacturer assesses
themselves; no notified body.

Chromium and Raspberry Pi OS are themselves Annex III products; their conformity is their
manufacturers' - the device's manufacturer has to keep them updated (below, R11).

## What is protected, against whom

**Assets**

| Asset | Why it matters |
|---|---|
| Members' data: card uid, name, orders, payments, balance, linked phones | personal data at a workplace (GDPR); the tally itself |
| The audit log | the record of who changed what; the base of the lockouts |
| Dashboard credentials and sessions, portal sessions | access to everything above |
| Device secrets: `.env` (DB password, Django key), the device CA's private key | full control, and the trust phones placed in the CA |
| The update path and its signing key | whoever controls it runs code on the device |
| Availability of the kiosk | the reason it exists - and a hung kiosk sends people back to paper |
| Backups on the USB stick | a copy of all data, outside the device |

**Attackers considered:** a colleague at the touchscreen with someone else's card; a curious
or mischievous person on the same network or WiFi; someone who finds a lost phone or backup
stick; a malicious or broken update; a compromised upstream package. **Not considered:** an
attacker with prolonged physical access and tools (opening the case, reading the SD card) - a
device in an office kitchen cannot defend against that, and it is stated as a limit.

## Method

Each threat gets a **likelihood** (1 rare - 3 likely in a device's life) and an **impact**
(1 annoyance, correctable from the audit log - 2 personal data disclosed or the tally
falsified unnoticed - 3 control of the device or its network neighbours). Risk = likelihood ×
impact: 1-2 **low**, 3-4 **medium**, 6-9 **high**. The residual risk is rated with the
existing measures in place; *accepted* means the operator is told in the manual or here.

## Risk register

| ID | Threat | Existing measures | L | I | Residual | Action |
|---|---|---|---|---|---|---|
| R1 | Ordering on someone else's account with their card or a copied MIFARE uid | card is a bearer token by design; every booking in the audit log, correctable in "Bookings"; it is a tally, not money | 2 | 1 | low | accepted, stated in SECURITY.md |
| R2 | Driving the kiosk from the network (fake taps, link QR codes for others) | `/kiosk/` only on `127.0.0.1:8443`, Caddy 404s it on 443; taps only via the internal MQTT bridge | 1 | 2 | low | - |
| R3 | Taking over a member's portal (photographed or intercepted link QR, guessed token) | 256-bit token, 5 min, single use, only while the card is at the kiosk; rate limit 10/15 min per address; devices listed and revocable | 1 | 2 | low | accepted, stated in SECURITY.md |
| R4 | Dashboard login by guessing or reusing passwords | no default password (generated per device), BSI-style password rules, lockout, idle logout, PBKDF2 hashes | 1 | 3 | low | - |
| R5 | A manager exceeding their role | system actions `@superuser_required`; audit log | 1 | 2 | low | - |
| R6 | Web attacks: XSS, CSRF, SQL injection, host header | template auto-escaping, CSRF middleware, ORM only, `ALLOWED_HOSTS`, `Secure`/`HttpOnly` cookies; test suite | 1 | 3 | low | - |
| R7 | Eavesdropping on the network or the WiFi | HTTPS with a device CA, WPA2 on the access point; nothing loaded from or sent to external hosts | 1 | 2 | low | - |
| R8 | Dashboard published to the internet | not foreseen by the product; lockout and password rules still hold | 1 | 3 | low | manual: say not to |
| R9 | A forged or tampered update bundle | Ed25519-signed manifest, per-file SHA-256, platform and space check, forward only; key never in CI | 1 | 3 | low | - |
| R10 | Signature check switched off on a device in use | labelled "development only" in the provisioning tool; permanent warning on the update page | 1 | 3 | low | - |
| R11 | Known vulnerabilities in the OS, Chromium, the kernel or SSH on an offline device | `apt` at installation; a bundle *can* carry OS packages (`--with-os-packages`), but the release bundles don't, and only `install/packages.txt` and its dependencies are in it - not the kernel, firmware, `openssh-server` | 2 | 3 | **high** | A1 |
| R12 | Known vulnerabilities in the app's own images | `pip-audit` + Grype fail CI on anything fixable; Dependabot weekly; exceptions documented in `.grype.yaml` | 1 | 3 | low | assess the two CPython CVEs for exploitability (A5) |
| R13 | Known vulnerabilities in third-party images (postgres, mosquitto, caddy) | only reported by CI; floating tags, newest at build time | 2 | 2 | medium | A4 |
| R14 | SSH with the Imager's password, reachable from the network | outside CoffeePy; SECURITY.md advises a key or a strong password | 2 | 3 | **high** | A2 |
| R15 | Lost or stolen backup stick | written to one registered stick only; optional AES-256 (`BACKUP_PASSPHRASE`) - **off by default** | 2 | 2 | medium | A3 |
| R16 | Stolen device or SD card read out | `.env` root-only; no disk encryption | 1 | 2 | low | accepted (physical access out of scope), stated here |
| R17 | Theft or loss of the device CA key | only on the device, kept by the installer; never leaves it | 1 | 2 | low | - |
| R18 | Data loss by a broken update, restore or a power cut | pre-update dump, automatic rollback, journaled recovery at boot, restore in one transaction, 100 % coverage on that code; upgrade test across releases | 2 | 2 | medium → low | - |
| R19 | Kiosk unavailable (crashed browser, hung stack) | watchdog (max. 3 steps/h), browser restart on missing poll, health check | 2 | 1 | low | - |
| R20 | Falsified history (bookings changed, entries hidden) | every change audited with actor and address; audit entries can't be edited in the UI; clock only goes forward | 1 | 2 | low | - |
| R21 | Harm to other devices (the box used as a stepping stone) | only Caddy publishes ports; no forwarding from the AP; web container without Docker socket or block devices; host helpers take validated file requests only | 1 | 3 | low | - |
| R22 | Misuse as a POS/payment system | README disclaimer; no money is moved; payment links only open the member's own app | 1 | 2 | low | - |
| R23 | Compromised dependency or CI (supply chain) | pinned and hashed actions, zizmor, gitleaks, Dependabot, SBOM per release, signing outside CI | 1 | 3 | low | - |

## Annex I part I - security requirements

| Requirement (Annex I, part I) | How CoffeePy meets it | Gap |
|---|---|---|
| (1) security appropriate to the risks | this assessment; [SECURITY.md](SECURITY.md) | - |
| (2)(a) no known exploitable vulnerabilities when made available | CI audit fails on every fixable finding; Dependabot | OS packages (R11), third-party images (R13), CPython exceptions not yet assessed (A5) |
| (2)(b) secure by default configuration, reset to original state | no default passwords, ranking and pillory off, kiosk local only, signature check on; reset in System (`coffee/purge.py`) | backups unencrypted by default (A3) |
| (2)(c) security updates, automatic by default with opt-out where applicable; users notified | signed offline bundles via the dashboard. *Automatic* updates are not applicable: the device is offline by design and has nothing to fetch from | no notice of a newer release on the device - operators must watch GitHub (A6) |
| (2)(d) protection from unauthorised access, reporting it | Django auth, roles, lockout, idle logout, card/portal tokens; failed logins and invalid links in the audit log | - |
| (2)(e) confidentiality of data at rest and in transit | HTTPS everywhere, WPA2; backup encryption optional | data at rest on the SD card unencrypted (R16, accepted); backup default (A3) |
| (2)(f) integrity of data, programs and configuration, reporting corruption | signed updates, checksummed backups read back, one-transaction restore, Caddy's hashed config files, audit log | - |
| (2)(g) data minimisation | "First L." names, no e-mail or phone, no tracking; see [PRIVACY.md](PRIVACY.md#what-is-processed) | - |
| (2)(h) availability, resilience against denial of service | watchdog, auto-recovery, rate limits on logins and links | no general request rate limit at Caddy - low, the device is on a LAN |
| (2)(i) minimise negative impact on other devices and networks | offline, no outgoing connections, no forwarding | - |
| (2)(j) limit attack surfaces | one published port (+ local 8443), no Django admin, web container without host access | SSH (A2) |
| (2)(k) reduce the impact of an incident | containers, least-privilege host helpers, per-device secrets, rollback | - |
| (2)(l) record and monitor security-relevant activity, with an opt-out | audit log of every change, retention `AUDIT_RETENTION_DAYS`; the opt-out the Annex asks for is the retention setting - switching the log off would also switch off the lockouts | - |
| (2)(m) remove all data and settings securely, transfer data | reset in System; backup and restore to move the data to another device | reset deletes rows, not the SD card's blocks - for disposal, wipe or destroy the card (A7) |

## Annex I part II - vulnerability handling

| Requirement (Annex I, part II) | How CoffeePy meets it | Gap |
|---|---|---|
| (1) identify components, SBOM | CycloneDX SBOM per image with each release (Syft) | the host's Raspberry Pi OS packages are not in it (A1) |
| (2) address vulnerabilities without delay, with security updates | Dependabot, CI audit, release on every push to `master` | OS packages (A1) |
| (3) regular tests and reviews | test suite on SQLite and PostgreSQL, coverage gates, lint, upgrade test on the real device | - |
| (4) disclose fixed vulnerabilities (description, impact, severity, remedy) | - | no advisories yet (A6) |
| (5) coordinated vulnerability disclosure policy | SECURITY.md asks for an issue without details | no private channel, no policy (A6) |
| (6) contact address for reports | the maintainer via GitHub | no stated address (A6) |
| (7) secure distribution of updates | signed manifest, checksums, forward-only, key outside CI | - |
| (8) updates free of charge, with advisory messages | MIT, releases free on GitHub; release notes | advisories (A6) |

## Open actions

For the repository, in order of residual risk. A manufacturer of a ready-built device would
have to close A1, A2 and A6 before placing it on the market, and add their own: a support
period of at least five years (Art. 13(8)), Annex II user information, Annex VII technical
documentation, the EU declaration of conformity and CE marking, and a process to report
actively exploited vulnerabilities to ENISA's single reporting platform within 24 hours
(Art. 14).

| ID | Action | For |
|---|---|---|
| A1 | Release bundles carry the OS's security updates: build them `--with-os-packages`, widen the set to the base system (kernel, firmware, `openssh-server`, `openssl`, `libc6`, ...), and add the host's packages to the SBOM | R11, (2)(a), II(1)-(2) |
| A2 | Installer hardens SSH: key-only by default, or offer to switch it off; the provisioning tool asks | R14, (2)(j) |
| A3 | Make backup encryption the default (the provisioning tool proposes a passphrase), or warn in the dashboard while backups are unencrypted | R15, (2)(b), (2)(e) |
| A4 | Pin third-party images by digest and let Dependabot move them; fail CI on fixable findings in them too | R13, (2)(a) |
| A5 | Record for CVE-2025-15367 and CVE-2026-12345 whether the affected CPython code is reachable from the app, in `.grype.yaml` | R12, (2)(a) |
| A6 | Vulnerability handling: enable GitHub private vulnerability reporting, rewrite SECURITY.md's last paragraph as a disclosure policy with a contact and response times, publish GitHub security advisories for fixed issues; optionally the dashboard shows the release date of the running version so an admin sees its age | II(4)-(6), (8), (2)(c) |
| A7 | Manual: how to dispose of a device or SD card, and not to expose the device to the internet | (2)(m), R8 |

## Review

This assessment is revisited - and its date above changed - when any of these change: a new
interface or published port, the update or backup path, authentication or roles, a new
category of personal data, a new third-party component, a vulnerability report, or before a
device is supplied commercially.
