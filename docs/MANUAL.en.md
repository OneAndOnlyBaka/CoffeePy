# CoffeePy – User manual

*[Deutsche Fassung](MANUAL.de.md)*

CoffeePy is the tally sheet for a shared coffee machine – without the sheet: whoever takes
a coffee holds their card to the reader next to the machine and taps what they're having on
the touchscreen. CoffeePy keeps count, shows everyone their balance, takes payments, and has
a dashboard in the browser for running it. Members can link their phone to see their stats
and pay from there.

> **Not a point-of-sale system.** CoffeePy only replaces the paper tally sheet. It is not a cash
> register and sells nothing: no receipts, no tax handling, no tamper-proof fiscal records, and
> no payment processing – a payment entered on the kiosk or the phone is a note that someone
> says they paid, and the pay links merely open the member's own app. The balances are an
> informal tally among the people sharing the machine, just like the sheet was. Where coffee is
> sold commercially, a real POS system is needed. CoffeePy comes without any warranty (see the
> [LICENSE](../LICENSE)).

This manual covers every screen. Technical details (architecture, development, internals)
are in the [README](../README.md).

**Contents**

1. [What you need](#1-what-you-need)
2. [Installation](#2-installation)
3. [First steps](#3-first-steps)
4. [The kiosk](#4-the-kiosk)
5. [The phone portal](#5-the-phone-portal)
6. [The dashboard](#6-the-dashboard)
7. [Running and maintaining it](#7-running-and-maintaining-it)
8. [When something goes wrong](#8-when-something-goes-wrong)

---

## 1. What you need

| Part | Notes |
|---|---|
| Raspberry Pi | **3 Model B or newer** (3, 3+, 4, 5, Zero 2 W). A 1 GB Pi 3 is enough; a Pi 4 with 2 GB+ has more headroom. |
| microSD card | 16 GB or more, preferably A1/A2 – it's the system disk. |
| Power supply | The official one for your model. Cheap supplies are the most common cause of flaky USB devices and touchscreens. |
| Touchscreen | 1024×600 (e.g. [Adafruit #2396](https://www.adafruit.com/product/2396)). The official 7" display (800×480) works too. |
| NFC reader | **ACR122U** by Advanced Card Systems – the only supported reader. |
| Real-time clock | **DS3231** module with a CR2032 battery, so the Pi keeps the right time without internet. Wire it to I²C-1: VCC→pin 1, GND→pin 6, SDA→pin 3, SCL→pin 5. CoffeePy checks it regularly and warns when it is missing, doesn't answer or has lost the time. |
| Cards | Any MIFARE cards or key fobs (ISO 14443A) – one per member. Many company or transit cards work as well. |
| USB stick (recommended) | For automatic backups. Nothing to prepare: you set it up in the dashboard (see [Backups](#backups)) – that erases it. |

### Housing

![Housing](pictures/housing.jpg)

A 3D-printable housing for the Pi, the 1024×600 display and the reader is in
[housing/](../housing/): print an `LCD_*` frame and the matching `Card_*` holder from
`housing/3D/` (`_H` landscape, `_V` portrait, `_V_T1`/`_V_T2` for printers that need the frame
in two parts); the labels in `housing/labels/` finish it off. Any other housing works just as well.

---

## 2. Installation

You install from a Linux PC with the **provisioning tool**. All the Pi needs is a fresh
**Raspberry Pi OS** with SSH switched on.

### Preparing the Pi

1. Write **Raspberry Pi OS Lite (64-bit)** to the SD card with the
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. In the Imager's settings, set a **user name and password** and **enable SSH**. The Pi
   needs internet for the installation – a network cable is easiest, otherwise enter your WiFi there.
3. Insert the card, connect display, reader and clock, power the Pi on.

> A network cable is the better choice: at the very end CoffeePy can set up its own WiFi,
> and a connection over the Pi's previous WiFi then drops.

### The provisioning tool

In the CoffeePy directory on your PC:

```bash
./scripts/provision.sh
```

It needs nothing but Bash, SSH and tar, and changes nothing on your PC. Use the arrow keys,
**Enter** and **Esc**.

**Language.** First you pick the tool's language. It is also what the tool suggests as the
device's default language.

![Language](pictures/manual/de/provision-language.png)

**Welcome**, then the **Pi's address** (as your router shows it, or `raspberrypi.local`), the
**user** and their **password** from the Imager. The password is kept in memory only and never
written anywhere.

![Welcome](pictures/manual/en/provision-welcome.png)

![The Pi's address](pictures/manual/en/provision-host.png)

![Password](pictures/manual/en/provision-password.png)

**Checking the Pi.** The tool shows model, system, memory, whether the Pi has internet and how
you are connected – and warns when something doesn't fit (too little memory, no internet, a
connection over the Pi's own WiFi).

![Checking the Pi](pictures/manual/en/provision-check.png)

If CoffeePy is already installed on the Pi, the tool says so and takes its settings as the
defaults. The database password and internal keys always stay as they are – so you can run
the tool again at any time to change settings or bring in a new version.

![Existing installation](pictures/manual/en/provision-existing.png)

**Settings.** Everything that goes into the Pi's configuration, on one page. Pick a line to
change it:

![Settings](pictures/manual/en/provision-settings.png)

| Setting | Meaning |
|---|---|
| Language | The language kiosk, dashboard and portal start in. Every device can still switch for itself. |
| Admin account | User name and password for the dashboard. A random password is suggested and shown at the end. Only applies when the account is **first** created – change an existing one in the dashboard. |
| WiFi access point | The Pi opens its own WiFi for phones to reach dashboard and portal. Turn it off if the Pi stays on your existing network. At every start it picks the channel (1, 6 or 11) the neighbouring networks disturb least, at the highest transmit power allowed. |
| Address for phones | The address in QR codes and in the certificate. `192.168.4.1` with the access point, otherwise the Pi's (preferably fixed) address on your network. |
| Time zone | For the clock and the statistics by hour and weekday. |
| Backups & audit log | Whether the backups on the USB stick are **encrypted** (a passphrase of at least 12 characters – keep it somewhere safe, no backup can be restored without it), and for how many days the **audit log** is kept (90 by default). How often backups are made is set in the dashboard. |
| Updates | Which **signature** an update has to carry: CoffeePy's key, **your own key**, or **no check** (see [Your own signing key](#your-own-signing-key)). |
| Display | Screen mode. The documented 1024×600 display doesn't report its resolution, so it needs the preset. |
| Web workers | Processes serving the web app: 2 for a Pi 3, 3–4 for a Pi 4 or 5. |
| Install from | Internet, or an offline bundle (for Pis without internet, see the README). |

Input is checked right away – a WiFi password, for instance, needs at least 8 characters:

![WiFi access point](pictures/manual/en/provision-wifi.png)

![An input being checked](pictures/manual/en/provision-validation.png)

![Updates](pictures/manual/en/provision-updates.png)

**Installing.** *Install CoffeePy* starts it. The installation runs on the Pi itself, the tool
only shows its progress – if the connection drops or you close the window, it carries on.
It takes 20–40 minutes, mostly for building the app.

![Installing](pictures/manual/en/provision-progress.png)

**Done.** Finally the tool reboots the Pi (clock, display and kiosk only take effect then),
checks that CoffeePy answers over HTTPS, and shows the credentials. It can save them to a file
only you can read.

![Done](pictures/manual/en/provision-done.png)

> **For several devices** the tool also runs without questions:
> `./scripts/provision.sh --answers answers.txt --unattended`. The keys the file knows are
> listed at the top of the script.

---

## 3. First steps

### Connecting a phone

With the access point, join the WiFi **CoffeePy** (the tool shows the password at the end,
and the kiosk shows it as a QR code, see [Admin access](#admin-access)). Then open
`https://192.168.4.1/` – without the access point, the address you gave during setup.

### Installing the certificate

CoffeePy is only reachable over HTTPS. The device signs its certificate itself, so the
browser warns on the first visit. To get rid of the warning, install the **device
certificate** once per phone or PC: it's linked on the login page and in the portal as
*Download certificate* (directly: `https://192.168.4.1/certificate/`).

- **iPhone/iPad:** open the file → install it under *Settings › Profile Downloaded* → then
  enable the CoffeePy certificate under *Settings › General › About › Certificate Trust Settings*.
- **Android:** *Settings › Security › More settings › Install certificate › CA certificate*
  and pick the downloaded file.
- **Windows:** open the file → *Install certificate* → store *Trusted Root Certification Authorities*.
- **macOS:** open the file (Keychain Access) → double-click the certificate → *Trust: Always Trust*.

If you uploaded your own certificate in the dashboard (see [Certificate](#certificate)),
this isn't needed as long as it comes from an authority your devices already trust.

### Logging in to the dashboard

Open `https://192.168.4.1/` and log in with the admin account. You switch the language below
the login form, and later with **DE/EN** at the top right. If the password doesn't meet the
[login rules](#login--passwords) (any more), the dashboard asks for a new one right after
logging in.

![Login](pictures/manual/en/dashboard-login.png)

### Setting up

Before the kiosk takes orders it needs at least one **coffee sort** – until then it shows
*Setup needed* with QR codes for WiFi and dashboard.

1. Add **coffee sorts**: name, price and machine strokes (how many cups the machine pours for
   it – a jug counts two).
2. Optionally add **payment methods** (PayPal, PayPal Pool, WERO or a custom link). Cash
   always works.
3. You don't need to add **members**: an unknown card on the kiosk leads straight to
   signing up with a name (see [Settings](#settings)).
4. Plug in a **USB stick** and set it up under **Backup** – from then on the device backs
   itself up (see [Backups](#backups)).

### Moving over from version 1

When CoffeePy replaces a device running the old version 1, you take its data over once –
best at the end of a day when nobody books any more:

1. **Take the database from the running device, not from a copy.** Switch the old device off
   once the last booking is made. The data is on its SD card in
   `/home/<user>/CoffeePy/coffee.db`; a Linux computer reads the card directly, or fetch the
   file over SSH beforehand. An older copy or a backup from the old stick is only a fallback:
   whatever was booked after it is missing otherwise.
2. On the dashboard, under **Import legacy database**, upload the file and tick **Clear
   existing members, cards, … first** – otherwise test bookings and a second import end up
   in the books twice.
3. Look at the newest entries under **Bookings**: if they match the last day on the old
   device, everything is there.
4. **Check the time** (dashboard → **System time**). The clock module usually moves over too,
   and with it the old device's time – which can be wrong after long running without a
   network.
5. **Reconnect the phones:** the WiFi has a new name and a new password, and every phone
   needs the [certificate](#installing-the-certificate).
6. **Announce the new names:** whoever isn't already called "First L." or a short code like
   "AB" – with version 1 almost nobody – is asked for a name at their first coffee on the new
   device (see
   [Settings](#settings)). That takes half a minute at the touchscreen; a note beforehand saves
   puzzled faces in the queue.
7. Set up a **backup stick** (see [Backups](#backups)). Setting up erases the old device's
   stick – if you want to keep its backups, use a new one.

Keeping the old device's SD card until the first weeks have gone well costs nothing.

---

## 4. The kiosk

The touchscreen has four sections, in the bar on the left. Round buttons sit top right:
the **wrench** is the [admin access](#admin-access) – it isn't meant for members –, **DE/EN**
switches the kiosk's language, and a **blinking red** button appears when something on the
device needs looking after:

| Sign | Meaning |
|---|---|
| Red, database symbol | **Backups are not running** – the backup stick is missing, or the last backup failed. Details under **Backup** in the dashboard. |
| Red, clock symbol | **The clock module (RTC) is faulty** – missing, not answering, or its battery is flat –, or **the clock was wrong and has been put forward**. Details on the dashboard's **Overview**. |

![Warning sign on the kiosk](pictures/manual/en/kiosk-device-alarm.png)

Apart from the ranking, every section needs a card. Holding up a card logs you in until you're
done, or logs you out automatically after 20 seconds without input. Tapping a section in the
bar logs out too. **Pay & Stats**, **Settings** and the **Ranking** go back to **Order** by
themselves after 20 seconds without a tap – so the next person always finds the kiosk ready to
order.

### Order

The start screen. It explains what happens when you hold up your card and shows the time.

![Order – apply your card](pictures/manual/en/kiosk-wait-order.png)

After the card, the kiosk greets you by name. Your favourite coffee is preselected; tap another
one to switch. **Order now** books right away, **Cancel** backs out, and without any input it
books automatically after 20 seconds. Picking a different sort turns the automatic booking off
– then you confirm yourself. The medals next to your name show a place in the top 3 – all time
and this month. Whoever still owes money from last month gets a reminder here.

![Order](pictures/manual/en/kiosk-order.png)

Then a thank-you – with a different animation depending on how many cups it's been today.

![Thank you](pictures/manual/en/kiosk-thank-you.png)

### Pay & Stats

![Pay & Stats – apply your card](pictures/manual/en/kiosk-wait-pay.png)

Shows your cups in total (**CCT**) and this month (**CCM**) and your **balance**. If it's
negative, the amount owed is already in the field – enter what you paid, pick how, and tap
**Pay**. With payment methods that have a link, you can also pay what you owe straight from
your phone via QR code.

![Pay & Stats](pictures/manual/en/kiosk-pay.png)

### Settings

![Settings – apply your card](pictures/manual/en/kiosk-wait-settings.png)

Change your **name** and **favourite coffee** here and tap **Save**. Type directly, or use
the on-screen keyboard via **Keyboard**. – it has umlauts, ß, hyphen and full stop too.

The name is **first name and the first letter of the last name**, like *Anna B.*, or a **short
code** of two to four capitals, like *AB* – so everyone knows who is on the ranking and the
pillory. The kiosk adds capitals and the full stop itself (*anna b* becomes *Anna B.*). It refuses placeholders (*Test*, *Name*, *Coffee* …), keyboard
mashing (*Asdf*) and a name somebody already has – a second letter helps then: *Anna Be.*
Whoever's name doesn't fit – including those still named after their card number – is **asked
for a name first** at their next coffee (*Who are you?*) and orders after that; saving goes
straight on to the order. The field already holds what can be made of the old name – *Anna*
becomes *Anna*␣, only the letter is missing. A name that only lacks the form (*Anna B* without
the full stop) is put right by the kiosk without asking.

![Settings](pictures/manual/en/kiosk-settings.png)

![On-screen keyboard](pictures/manual/en/kiosk-keyboard.png)

A card the kiosk doesn't know yet lands here automatically: saving creates the new member.

![New card](pictures/manual/en/kiosk-new-card.png)

**Link this phone** shows a QR code that connects your phone to your account (see
[phone portal](#5-the-phone-portal)). It's valid for five minutes and works once. With the
access point, the WiFi's QR code sits next to it – the phone has to join that first.

![Link your phone](pictures/manual/en/kiosk-link-phone.png)

### Ranking

No card needed. Three tabs at the top, each with its own retro animation; the names are in the
bar underneath. Counted are cups – a jug is two.

- **All time** – the podium: the top 3 since the start, first in the middle.

  ![Ranking all time](pictures/manual/en/kiosk-ranking-alltime.png)

- **This month** – the month isn't over yet: the current top 3 stand by while a fourth runs
  up, vaults over a swivel chair to the coffee machine and waits for the judges' scores.

  ![Ranking this month](pictures/manual/en/kiosk-ranking-month.png)

- **Pillory** – everyone who still owes money up to last month, as a walk of shame; their
  names and amounts run through the bar underneath. When everyone has paid, the banner says
  **NO SHAME** and the executioner stands on an empty scaffold with nothing to do.

  ![Pillory](pictures/manual/en/kiosk-ranking-pillory.png)

Opening the ranking always starts on **All time**.

### Admin access

The round wrench top right is for whoever looks after the device: it shows two QR codes, one to
join the device's WiFi and one that opens the dashboard – the touchscreen itself has no keyboard,
after all. Members don't need it; they link their phone under **Settings**.

![Admin access](pictures/manual/en/kiosk-setup-popup.png)

### "We'll be right back"

During an installation, an update or a restore – and whenever the app briefly doesn't answer –
the kiosk shows this screen and takes no input. Once the work is done, it reloads by itself;
the same goes for an error page.

![Maintenance](pictures/manual/en/kiosk-maintenance.png)

---

## 5. The phone portal

Every member can link their phone: on the kiosk, hold your card up under **Settings**, tap
**Link this phone** and scan the QR code with the camera. The phone then stays logged in for
six months (extended every time it's used). Switch the language top right; it applies to this
phone only.

**Home** shows cups, balance and the latest orders and payments. The device certificate is at
the bottom of the page.

![Portal home](pictures/manual/en/portal-home.png)

**Pay** opens your payment app with the amount owed, or records a payment you've already made.

![Portal pay](pictures/manual/en/portal-pay.png)

**Stats** compares you with everyone else: cups per month, time of day, weekday and sort.

![Portal stats](pictures/manual/en/portal-stats.png)

**Cards** lists your cards; *Add a new card* links a replacement by holding it to the kiosk.
**Devices** lists your linked phones – log out a lost one there.

![Portal cards](pictures/manual/en/portal-cards.png)

![Portal devices](pictures/manual/en/portal-devices.png)

A link that has expired or was already used ends up here – just create a new one on the kiosk.

![Link no longer valid](pictures/manual/en/portal-link-invalid.png)

### How linking works – and why it is safe

The portal has no passwords. Instead the kiosk hands the phone a **one-time link**:

1. **Only with your card, only at the device.** The QR code under **Link this phone** only
   exists while you are logged in on the kiosk with your card. The kiosk itself can only be
   reached from the touchscreen, not over the network – nobody can request a link for
   someone else from their phone or PC.
2. **A random token, not a password.** The link contains 256 bits from a cryptographic random
   generator (43 characters). It can't be guessed or worked out, not even with unlimited
   attempts in the five minutes it is valid.
3. **Five minutes, once.** The link expires after five minutes and is deleted the moment it is
   opened – a second scan gets *Link no longer valid*. A new link replaces any older one the
   member hasn't used yet.
4. **The phone gets its own login.** Opening the link starts a fresh session on that phone:
   a cookie that only travels over HTTPS (encrypted, checked against the device certificate)
   and that the page's scripts can't read. It stays valid for six months and is extended
   every time it's used; the link itself is worthless from then on.
5. **Everything can be undone.** Every linked phone appears under **Devices** in the portal
   and can be logged out there; an admin does it for all of a member's phones with **Revoke
   devices**, and a member marked as departed is logged out everywhere. Issuing the link,
   every login and every failed attempt land in the [audit log](#more-system-pages).

**What a phone can do there** – and therefore what a stranger could do with someone else's
phone: see that member's cups, balance and bookings, compare them with the others' totals,
record a payment *they say* they made, and start adding a card – which then still has to be
held to the kiosk. No money moves: a payment entered in the portal is a note like on the
kiosk, it shows up under **Bookings** and in the audit log, and an admin can correct it.
Dashboard and kiosk can't be reached with a portal login.

**The one thing to keep in mind:** whoever photographs the QR code within those five minutes
and opens it first gets that member's portal instead of them. The member notices right away
(their own scan says *Link no longer valid*), the phone shows up under **Devices**, and one tap
logs it out again. So: scan it yourself, right away, and close the window afterwards.

---

## 6. The dashboard

The dashboard is at `https://<address>/dashboard/`. On a PC or tablet its pages are in the bar
on the left; on a phone the everyday ones are tabs at the bottom and everything else is under
**More**.

![Dashboard on a phone](pictures/manual/en/dashboard-phone.png)

There are two roles (see [Dashboard users](#dashboard-users)): **Managers** look after members,
bookings, coffee sorts and payment methods; **Admins** can also do everything under **System** –
users, login rules, backups, updates, certificate, audit log and the like. The admin account from
setup is an admin. There is no other administration interface.

**Overview** shows the most important numbers, buttons to enter a payment or add a member, and
the **Device** card: is the card reader online, are backups running, is the clock module (RTC)
fine, and under which address phones reach the device. Something red here needs looking after –
the same things blink red on the kiosk.

![Overview](pictures/manual/en/dashboard-home.png)

### Statistics and pillory

**Statistics**: cups and turnover over the last 12 months, sort distribution, cups by hour and
weekday (the titles name the busiest slot), price trend and payments by method. Cups count
machine strokes – a jug is two.

![Statistics](pictures/manual/en/dashboard-statistics.png)

**Pillory**: every member with a negative balance, counted up to last month – the same list
as on the kiosk.

![Pillory](pictures/manual/en/dashboard-pillory.png)

### Members

The list of all members with their cards. Per member:

- **Edit**: name, UID, favourite coffee. The name follows the same rules as at the kiosk
  (see [Settings](#settings)).
- *New name at next coffee*: this name doesn't fit the rules – the kiosk asks the member for
  a new one at their next coffee.
- **Add card**: the next card held to the kiosk becomes this member's – that's how someone gets
  a replacement card without ending up with a second account.
- **Revoke devices**: logs out all linked phones.
- **Mark as departed**: writes off the balance and blocks the cards; *Reactivate* undoes it.

![Members](pictures/manual/en/dashboard-members.png)

![Edit member](pictures/manual/en/dashboard-member-form.png)

**Merge members** helps when two accounts did end up existing: all orders, payments and cards
move to the account you keep, the other one is deleted.

![Merge members](pictures/manual/en/dashboard-merge.png)

Under **Names** (admins only) you enter **blocked first names** (or short codes), one per line – on top of the
usual placeholders, which are always refused. Meant for what only someone on the team would
think of (*Boss*, the coffee machine's name …). Whoever already goes by one of them is asked
for a new name at their next coffee.

### Bookings

Every coffee booked and every payment, newest first, filterable by member (with their balance).
Here you correct a wrong booking (**Edit**, **Delete**) and record a payment that didn't go
through the kiosk – **Enter payment**, e.g. cash put in the till. Every change is recorded in
the audit log.

![Bookings](pictures/manual/en/dashboard-bookings.png)

![Enter payment](pictures/manual/en/dashboard-payment-form.png)

### Coffee sorts and payment methods

**Coffee sorts**: what the kiosk offers – name, price and machine strokes.

![Coffee sorts](pictures/manual/en/dashboard-coffee-sorts.png)

**Payment methods**: PayPal needs the paypal.me name, PayPal Pool the pool's URL, WERO the
phone number; *Custom link* takes any address, optionally with `{amount}` for the amount owed.
These become the pay links and QR codes on kiosk and phone.

![Payment methods](pictures/manual/en/dashboard-payment-methods.png)

![Add payment method](pictures/manual/en/dashboard-payment-method-form.png)

### My account

Your name at the top right: your role, your last login, and **Change password**. Your own password is only changed here, never in the user list.

![My account](pictures/manual/en/dashboard-account.png)

### Dashboard users

Who may log in to the dashboard – admins only. **Add user** asks for a user name, the role
(**Admin** or **Manager**) and a first password; tick *Must set a new password at the first
login* to hand it over as a one-off password. Per user: **Edit**, **Set password** (for a
forgotten one), **Delete**. A user locked after too many failed logins is marked *locked*.

![Dashboard users](pictures/manual/en/dashboard-users.png)

![Add user](pictures/manual/en/dashboard-user-form.png)

### Login & passwords

The rules for logging in. The defaults follow the BSI's recommendation and can only be set
within sensible limits:

| Rule | Default | Possible |
|---|---|---|
| Minimum length | 12 characters | 8–64 |
| Character kinds (lower case, upper case, digits, symbols) | 3 | 1–4 |
| … or a long passphrase of at least | 20 characters with 2 kinds | off, or up to 128 |
| Lock after failed logins | 5 | 3–20 |
| Locked for | 15 minutes | 1–1440 |
| Logged out when idle for | 30 minutes | 5–480 |

Passwords are at most 128 characters long. When the rules get **stricter**, nobody is locked
out: whoever's password no longer meets them is asked for a new one at their next login.

![Login & passwords](pictures/manual/en/dashboard-password-policy.png)

### System

**System** shows the device's CPU, memory and disk.

![System](pictures/manual/en/dashboard-system.png)

**System time**: only needed when the clock module is missing or wrong. The new time is
also written to the clock module within ten minutes.

Without a network nobody puts the clock right – so the device itself makes sure it doesn't
go **back**: if it jumps back, or stands before the newest entries in the audit log (every
booking writes one), it is put forward again and the kiosk shows the red clock sign. The
overview then says from when to when. Check the time and set it here, or confirm it with
**The time is right** – the sign goes away. It is never put back: a clock that is ahead
can't be told from a right one. With internet access the device sets its clock itself anyway,
and this check stays out of it.

![System time](pictures/manual/en/dashboard-system-time.png)

#### Backup

Everything about the backup stick (see [Backups](#backups) for how it works):

- **This device's backup stick** – space, whether the backups are encrypted, **Back up now**,
  **Eject safely**, and the backups on it, each with **Download** and **Restore**. *checked*
  means its checksum is fine, *damaged* that it isn't.
- **USB sticks** – a stick that isn't set up yet: **Set up as backup stick**. A stick from
  before (of this device) or of another CoffeePy can be read, and taken over with **Use for
  this device**.
- **When and what is kept** – how often (every 15 minutes to once a day) and how many backups
  stay: the newest ones, then one per day, week and month.
- **Restore a backup** – from the stick, or **Restore from a file**.
- **Download a backup** – the whole device as it is right now, as a file, also without a stick.

![Backup](pictures/manual/en/dashboard-backup.png)

When restoring you choose **what**: the data (members, bookings, coffee sorts, dashboard logins),
the certificates and device CA (phones keep trusting the device), the device identity (its
backup stick becomes this device's) and the settings (language, address, time zone, log
retention). To confirm you type `DELETE`. Before anything changes, the current state is saved
to the stick; if the restore fails, nothing is changed. An encrypted backup needs the passphrase.

![Restore from a file](pictures/manual/en/dashboard-restore-upload.png)

#### Update

Upload an update bundle and follow it being installed (see [Updates](#updates)). **Back to …**
returns to the version before the last update. **Versions** lists exactly what runs on the
device: the CoffeePy version and its commit, Python, Django, the database, the operating
system, Docker, and every container with the id of its image – **Copy all** puts the whole list
on the clipboard, ready for a bug report.

![Update](pictures/manual/en/dashboard-update.png)

#### Certificate

Shows which certificate is in use. You can upload **your own certificate** (PEM, with chain,
plus the unencrypted private key); it's live within seconds. CoffeePy checks that key and
certificate belong together and warns if it doesn't cover the device's addresses. A button
goes back to the self-signed certificate. The kiosk on the device always keeps the device's
own certificate – a bad upload can never lock it out.

![Certificate](pictures/manual/en/dashboard-certificate.png)

#### More system pages

**Import legacy DB**: takes over the data of an old CoffeePy (V1) – a `coffee.db` or one of
its backups. The import keeps running in the background even if you close the page.

![Import](pictures/manual/en/dashboard-import.png)

**Audit log**: everything that happened on the device – logins and failed logins, every change
in the dashboard, bookings and settings on the kiosk and in the portal, backups, restores,
updates, the backup stick and the clock module. Per entry: when, who, from where, what, and
whether it succeeded. **Search** and the filters narrow it down, **Export (CSV)** downloads
what's shown. Entries are deleted after the retention period (90 days by default, see
[provisioning tool](#the-provisioning-tool)).

![Audit log](pictures/manual/en/dashboard-audit-log.png)

**Reset database** deletes **all** coffee data (members, cards, orders, payments, sorts,
payment methods, audit log). Dashboard logins stay. To confirm you have to type `DELETE` –
take a backup first!

![Reset database](pictures/manual/en/dashboard-reset.png)

---

## 7. Running and maintaining it

### Changing settings

Simply run the [provisioning tool](#the-provisioning-tool) again: it reads the existing
configuration, you change what you want, and it applies the change – the kiosk shows "We'll be
right back" meanwhile. Data, database password and certificate are kept.

### Backups

The device backs itself up to **one USB stick** that you set up for it:

1. Plug in a stick and open **Backup** in the dashboard.
2. **Set up as backup stick** – after a confirmation the stick is **erased**, formatted and
   marked as this device's. Backups start right away.
3. From then on the device makes a backup at the interval you set (hourly by default) and
   thins them out: the newest 48, then one a day for 14 days, one a week for 8 weeks, one a
   month for 12 months.

A backup holds the whole device: the data, the device CA and certificates, and its settings –
so a replacement device can take over completely. Every backup is read back and checked after
writing. With a passphrase from the [provisioning tool](#the-provisioning-tool) it is
encrypted (AES-256); without one, anyone who takes the stick can read it – a deliberate choice
when the stick sits inside the housing.

- **Stick pulled out?** The overview and the kiosk show that backups aren't running. Plug the
  **same** stick back in, and everything carries on as before.
- **New stick?** It has to be set up like the first one. The old one is forgotten: the device
  still reads it, but never writes to it again.
- **Restoring** is described under [Backup](#backup). A restore also replaces the dashboard
  logins – you may have to log in again afterwards.

### Updates

The device needs no internet for updates. On a PC with internet and Docker (or Podman):

```bash
./scripts/update_builder.sh
```

The **update package builder** asks what goes into the bundle – platform, the device's own
services, operating system packages for an offline device –, which signing key to use (it
creates one on the first run), and where the bundle goes. It builds and signs the bundle and
can upload it to a device's dashboard right away and follow it being installed.

![Update package builder](pictures/manual/en/update-builder.png)

Otherwise upload the bundle in the dashboard under **Update**. The device then:

1. checks the **signature** and every file's checksum, that the bundle is meant for this device,
   that there is room, and that it isn't older than what's installed (unless *Allow installing
   an older version* is ticked),
2. saves the database and the running version **to the SD card** (never to the backup stick),
3. installs it – the kiosk shows "We'll be right back" with a progress bar, the update page the log,
4. checks that the new version answers. If it doesn't, the previous version **goes back
   automatically**, data included.

**Back to …** on the update page returns to the version before the last update by hand. The
data then also goes back to the state before that update.

For scripts there is `./scripts/build_update_bundle.sh` (its options are listed at the top of
the script); an update also works from a PC through the provisioning tool.

#### Your own signing key

A device only installs updates whose signature matches its key – by default the key that comes
with CoffeePy (`install/update-signing.pub`). If you build updates yourself, use your own:

1. **Create a key pair** on the PC where you build the updates:
   `./scripts/update_signing_key.sh` – the private key goes to
   `~/.config/coffeepy/update-signing.key` (or the path you give it) and **never leaves that
   PC**; the public key is written to `install/update-signing.pub`.
2. **Put the public key on the device** – one of two ways:
   - in the [provisioning tool](#the-provisioning-tool) under **Updates › Own key**, pick the
     public key file (`.pub`). It ends up on the device as
     `/etc/coffeepy/update-signing.own.pub`, takes precedence over CoffeePy's key, and **no
     update replaces it**. **CoffeePy's key** there removes it again.
   - or install from your own checkout with your `install/update-signing.pub` committed –
     then it is the device's key from the start, and every update built from that checkout
     brings the same one along.
3. Build updates with that key: the builder asks for it (*Signing key*), the script takes
   `--key FILE`.

The fingerprint of the key in use is shown under **Update › Versions** (*Update signature*).

**Switching the check off completely:** in the provisioning tool **Updates › No check**, or
`UPDATE_VERIFY_SIGNATURE=false` in `/opt/coffeepy/.env` on the device. The device then
installs every bundle – signed, signed with another key, or not signed at all
(`build_update_bundle.sh --unsigned`). The checksums, the platform and the space are still
checked. Only do this when nobody you don't trust can log in to the dashboard as an admin:
whoever can upload an update can then run anything on the device.

### When the kiosk hangs

Every minute the device checks that the app answers and that the kiosk's browser still asks
it for new cards. If something hangs, it restarts it, a step at a time: the kiosk screen
first, then, if that didn't help, the app. If the app doesn't answer for three minutes, it
restarts that right away. Each takes a minute or two and shows up in the **Audit log** as
*watchdog.kiosk_restart* / *watchdog.stack_restart*.

It stays out of updates, restores and imports and the first minutes after switching on, and
after three restarts within an hour it gives up (*watchdog.gave_up*): a restart doesn't help
then, and it is time to look at the audit log. Pulling the plug is only left for when even
that has no effect.

### Language

You pick the default language during setup. Every device can switch for itself: the kiosk
with its **DE/EN** button, phones and PCs at the top right of the portal and of the dashboard
(or below the login form). Every device remembers its own choice.

---

## 8. When something goes wrong

| Problem | Solution |
|---|---|
| The browser warns about the site. | Install the device certificate once (see [above](#installing-the-certificate)). |
| **Bad Request (400)** in the browser. | The device doesn't know the address you're using. Use the address from setup (`192.168.4.1`, `coffeepy.local`), or run the provisioning tool over the new address – it adds it. |
| After switching on, the screen is black, then white, for a minute. | Normal: the Pi starts first, then the browser. The kiosk appears after about a minute. |
| The kiosk went black for a moment and started again. | The device restarted a hanging kiosk or the app by itself (see [When the kiosk hangs](#when-the-kiosk-hangs)); the **Audit log** says which. If it happens often, that is where to look for why. |
| *Card reader not available* | Check the reader's cable and restart the device. |
| A card isn't recognised. | Hold it flat and still; a card marked as departed is turned away. |
| Red **database** sign on the kiosk | Backups aren't running: plug the backup stick back in, or look at **Backup** in the dashboard for the error. |
| Red **clock** sign on the kiosk, or the time is wrong | If the overview has a *Time – corrected* row, the clock went back and was put forward: check it under **System time** and set or confirm it. Otherwise the *Clock module (RTC)* row says why. *Not found* / *Does not answer*: check the module and its wiring. *Had lost the time*: replace its battery (CR2032), then correct the time under **System time** – the sign goes once the module has kept the time through a restart. |
| "We'll be right back" doesn't go away. | Is an update or a restore running? The dashboard's **Update** or **Backup** page shows its progress. A status older than two hours is ignored; when in doubt, restart the device. |
| Login refused although the password is right | Too many failed attempts lock the account for a while (15 minutes by default) – wait, or have an admin set a new password under **Dashboard users**. |
| An update is refused | The update page says why: no or a wrong signature (see [Your own signing key](#your-own-signing-key)), a bundle for another platform, not enough space, or an older version than the installed one. |
| The phone can't reach the dashboard. | Is it connected to the device's WiFi? Turn mobile data off briefly – some phones leave a WiFi without internet otherwise. |
