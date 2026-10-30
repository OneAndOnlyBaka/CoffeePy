# Running CoffeePy in line with the GDPR

CoffeePy processes personal data about colleagues - who drinks how much coffee, when, and who
owes money. This page sets out what the software processes, where, for how long, and which of
its features help whoever runs it meet the GDPR (DSGVO). It describes the software; it is
**not legal advice**, and the duties lie with whoever operates the device - usually the
employer or the group that shares the machine, which then is the **controller**.

The manual has the short version for admins: [Privacy and the workplace](MANUAL.en.md#privacy-and-the-workplace).
The technical protections are in [SECURITY.md](SECURITY.md).

- [What is processed](#what-is-processed)
- [Where it is kept - and where it is not](#where-it-is-kept---and-where-it-is-not)
- [How long](#how-long)
- [Privacy by default](#privacy-by-default)
- [The rights of the people concerned](#the-rights-of-the-people-concerned)
- [Works council](#works-council)
- [Technical and organisational measures](#technical-and-organisational-measures)
- [Checklist before going live](#checklist-before-going-live)

## What is processed

| Data | Of whom | Why | Where it comes from |
|---|---|---|---|
| Card uid (number of the NFC card) | members | to recognise a member at the kiosk | the card tap |
| Name - "First L." or a short code like "AB" | members | to show whose account it is | the member at the kiosk, or an admin |
| Orders: time, coffee sort, price, cups | members | the tally | the kiosk |
| Payments: time, amount, method | members | the tally | kiosk, portal, dashboard |
| Balance, rankings, statistics | members | calculated from the above, never stored separately | - |
| Linked phones: browser description, linked and last seen | members | to let the member see and log out their phones | the portal |
| Dashboard users: user name, password hash, role | admins, managers | access to the dashboard | an admin |
| Audit log: time, who, what, client IP address, outcome | everyone who changes something | traceability and security (lockouts) | every change |

**Not processed:** e-mail addresses, phone numbers, full names (the name rules ask for a first
name and an initial, or a code), bank details, location, anything about a phone beyond its
browser's description. Payment links only open the member's own payment app (PayPal, ...);
CoffeePy never learns whether or what was paid there - a payment is what the member enters.

**Purpose:** a shared tally of who drank what and who paid - replacing the paper list next to
the machine. Ranking and pillory are an optional extra on top (see below). Possible legal
bases are an agreement among those who share the machine (Art. 6 (1) (b) GDPR) or a
legitimate interest in settling the costs (Art. 6 (1) (f)); in an employment context also
§ 26 BDSG and, where it exists, a works agreement. Which one applies is the controller's
decision.

## Where it is kept - and where it is not

- **Only on the device**: the database on the Pi's SD card, and backups on its USB stick.
- **No cloud, no third parties, no processors.** The device runs offline and loads nothing
  from outside - no CDN, no fonts, no analytics. No data leaves it unless an admin downloads
  an export or a backup, or takes the stick.
- **Phones** only see the data of the member they were linked for, over HTTPS within the
  device's network.

## How long

| Data | Kept | Deleted by |
|---|---|---|
| Members, orders, payments | until the member is deleted, or the database reset | an admin (Members → Delete; System → Reset) |
| A departed member's data | kept for the history, hidden from rankings and pillory | deleting the member |
| Audit log (with IP addresses) | `AUDIT_RETENTION_DAYS`, **90 days** by default | automatically, hourly |
| Raw card taps (`NfcScan`) | about **one hour** | automatically |
| One-time phone links | **5 minutes**, or until used | automatically |
| Phone sessions | **6 months** after the last use | automatically; at once by logging the phone out, revoking, or marking the member as departed |
| Backups on the stick | by generations: the newest 48, one per day for 14 days, per week for 8 weeks, per month for 12 months (adjustable in the dashboard) | automatically; or by removing the stick |
| Copies before a restore | the newest few, on the stick | automatically |
| Database copies before an update | the last **3** updates, on the SD card | automatically |

**Order and payment history has no automatic deletion.** CoffeePy can't know when a debt is
settled for good or how long the group wants its history. The controller should decide on a
period - for example: delete members a few months after they left and their balance is
settled - and do it in the dashboard. Remember that backups still contain deleted data until
they rotate out (up to 12 months with the defaults).

## Privacy by default

- **Ranking and pillory are off on a new device** (System → kiosk boards). Both show personal
  data to everyone standing at the machine; switching them on is a deliberate decision.
- **Short names.** "First L." by default, and anyone may use a short code like "AB" instead -
  the name rules accept it, and the ranking then shows only the code.
- **The kiosk shows a member's own data only after their tap**, and logs them out after 20
  seconds without a touch.
- **The portal shows only the member's own data**; comparisons with others are averages and
  shares, never another person's numbers.
- **Departed members** disappear from rankings and pillory, their cards stop working and their
  phones are logged out.
- **Statistics in the dashboard** are aggregates (per month, hour, weekday, coffee sort); only
  the pillory and the member pages show individuals, and only to dashboard users.

## The rights of the people concerned

| Right | How CoffeePy supports it |
|---|---|
| Information (Art. 13/14) | the controller informs; this page and the manual's privacy section describe the processing |
| Access (Art. 15) | members see their cups, balance and bookings in the portal and on the kiosk's Pay screen; a dashboard user can export all orders and payments as CSV (dashboard overview) and pick out one member's rows by their uid |
| Rectification (Art. 16) | members change their name at the kiosk (Settings); admins correct names and bookings in the dashboard, each correction logged |
| Erasure (Art. 17) | Members → Delete removes the member with their cards, orders, payments and linked phones; audit entries and backups follow within their retention periods |
| Restriction (Art. 18) | Mark as departed: cards blocked, hidden from rankings and pillory, data kept |
| Data portability (Art. 20) | the CSV exports |
| Objection (Art. 21) | a short code instead of a name; the ranking and pillory can be switched off for everyone |

## Works council

In Germany a technical facility that is *suited* to monitor employees' behaviour or performance
is subject to co-determination (§ 87 (1) no. 6 BetrVG) - whether monitoring is intended doesn't
matter. Consumption times, rankings and a list of debtors can be read that way. Where there is
a works council, involve it before the device goes live, and in particular before the ranking or
the pillory are switched on. The configurable parts - boards, name rules, audit retention,
backup generations, who has a dashboard login - are the natural content of an agreement.

## Technical and organisational measures

What the software brings (details in [SECURITY.md](SECURITY.md)):

- confidentiality: HTTPS everywhere; the kiosk reachable only at the device; dashboard logins
  with BSI-style password rules, lockout and idle logout; two roles; portal access only through
  a one-time link from the member's own card tap; optional backup encryption (AES-256);
- integrity: an audit log of every change with who, what and from where; signed updates;
- availability: automatic, verified backups on a USB stick; updates that roll themselves back;
  recovery after power cuts; a watchdog for the kiosk;
- data minimisation: short names, no contact data, card taps deleted after an hour, audit log
  after 90 days.

What the operator adds:

- keep the device and the backup stick physically secure (the stick holds everything,
  including the device's secrets in `.env`); set a backup passphrase if the stick may leave
  the building;
- give dashboard logins only to those who collect the money, and the admin role to as few as
  possible;
- protect the Pi's own SSH access with a strong password or a key;
- record the processing in the record of processing activities (Art. 30), with the retention
  periods above and your own for the order history;
- delete departed members once their balance is settled.

## Checklist before going live

1. Decide who is the controller and who administers the device.
2. Inform the members: what is recorded, why, who sees it, how long it is kept (Art. 13).
3. Involve the works council, where there is one.
4. Decide whether the ranking and the pillory are switched on - and tell people.
5. Set the audit retention (`AUDIT_RETENTION_DAYS`) and a backup passphrase in the
   provisioning tool if needed.
6. Set the backup generations in the dashboard to what you want to keep.
7. Fix a period after which departed members are deleted.
8. Hand out dashboard roles sparingly.
