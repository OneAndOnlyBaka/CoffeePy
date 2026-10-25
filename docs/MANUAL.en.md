# CoffeePy – User manual

*[Deutsche Fassung](MANUAL.de.md)*

CoffeePy is the tally sheet for a shared coffee machine – without the sheet: whoever takes
a coffee holds their card to the reader next to the machine and taps what they're having on
the touchscreen. CoffeePy keeps count, shows everyone their balance, takes payments, and has
a dashboard in the browser for running it. Members can link their phone to see their stats
and pay from there.

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
| Real-time clock | **DS3231** module, so the Pi keeps the right time without internet. Wire it to I²C-1: VCC→pin 1, GND→pin 6, SDA→pin 3, SCL→pin 5. |
| Cards | Any MIFARE cards or key fobs (ISO 14443A) – one per member. Many company or transit cards work as well. |
| USB stick (optional) | For automatic backups. Formatted `ext4` and labelled `COFFEEBAK` (`mkfs.ext4 -L COFFEEBAK /dev/sdX1`), it is mounted automatically. |

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

1. Write **Raspberry Pi OS Lite** to the SD card with the
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (the 32-bit one on a Pi 3).
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
| WiFi access point | The Pi opens its own WiFi for phones to reach dashboard and portal. Turn it off if the Pi stays on your existing network. |
| Address for phones | The address in QR codes and in the certificate. `192.168.4.1` with the access point, otherwise the Pi's (preferably fixed) address on your network. |
| Time zone | For the clock and the statistics by hour and weekday. |
| Backups | Regular database backups, by default to a USB stick. |
| Display | Screen mode. The documented 1024×600 display doesn't report its resolution, so it needs the preset. |
| Web workers | Processes serving the web app: 2 for a Pi 3, 3–4 for a Pi 4 or 5. |
| Install from | Internet, or an offline bundle (for Pis without internet, see the README). |

Input is checked right away – a WiFi password, for instance, needs at least 8 characters:

![WiFi access point](pictures/manual/en/provision-wifi.png)

![An input being checked](pictures/manual/en/provision-validation.png)

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
and the kiosk shows it as a QR code, see [below](#setup--network-info)). Then open
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
the login form, and later at the bottom of the left-hand bar.

![Login](pictures/manual/en/dashboard-login.png)

### Setting up

Before the kiosk takes orders it needs at least one **coffee sort** – until then it shows
*Setup needed* with QR codes for WiFi and dashboard.

1. Add **coffee sorts**: name, price and machine strokes (how many cups the machine pours for
   it – a jug counts two).
2. Optionally add **payment methods** (PayPal, PayPal Pool, WERO or a custom link). Cash
   always works.
3. You don't need to add **members**: an unknown card on the kiosk leads straight to
   signing up with a nickname.

---

## 4. The kiosk

The touchscreen has four sections, in the bar on the left. Two round buttons sit top right:
**DE/EN** switches the kiosk's language, the **wrench** is the admin access – it isn't meant
for members.

Apart from the ranking, every section needs a card. Holding up a card logs you in until you're
done, or logs you out automatically after 20 seconds without input. Tapping a section in the
bar logs out too.

### Order

The start screen. It explains what happens when you hold up your card and shows the time – if
it's visibly wrong, the real-time clock's battery is flat.

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

Change your **nickname** and **favourite coffee** here and tap **Save**. Type directly, or use
the on-screen keyboard via **Keyboard**.

![Settings](pictures/manual/en/kiosk-settings.png)

![On-screen keyboard](pictures/manual/en/kiosk-keyboard.png)

A card the kiosk doesn't know yet lands here automatically: saving creates the new member.

![New card](pictures/manual/en/kiosk-new-card.png)

**Link this phone** shows a QR code that connects your phone to your account (see
[phone portal](#5-the-phone-portal)). It's valid for five minutes and works once. With the
access point, the WiFi's QR code sits next to it – the phone has to join that first.

![Link your phone](pictures/manual/en/kiosk-link-phone.png)

### Ranking

No card needed. **Next** pages through the all-time top 7, this month's top 7 and the
**deposit pillory** – everyone who still owes money up to last month.

![Ranking all time](pictures/manual/en/kiosk-ranking-alltime.png)

![Ranking this month](pictures/manual/en/kiosk-ranking-month.png)

![Deposit pillory](pictures/manual/en/kiosk-ranking-pillory.png)

### Admin access

The round wrench top right is for whoever looks after the device: it shows two QR codes, one to
join the device's WiFi and one that opens the dashboard – the touchscreen itself has no keyboard,
after all. Members don't need it; they link their phone under **Settings**.

![Admin access](pictures/manual/en/kiosk-setup-popup.png)

### "We'll be right back"

During an installation or an update – and whenever the app briefly doesn't answer – the kiosk
shows this screen and takes no input. Once the work is done, it reloads by itself.

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

---

## 6. The dashboard

The dashboard is at `https://<address>/dashboard/`. Pages that concern the whole device
(System, Backup, Update, Certificate, Import, Reset, Audit log) are for **superusers** only –
that is, the admin account from setup.

**Home** shows the card reader's state, the address for other devices, and the exports (all
orders or payments as CSV).

![Dashboard home](pictures/manual/en/dashboard-home.png)

### Statistics and pillory

**Statistics**: cups and turnover over the last 12 months, sort distribution, cups by hour and
weekday (the titles name the busiest slot), price trend and payments by method. Cups count
machine strokes – a jug is two.

![Statistics](pictures/manual/en/dashboard-statistics.png)

**Pillory**: every member with a negative balance, counted up to last month – the same list
as on the kiosk.

![Pillory](pictures/manual/en/dashboard-pillory.png)

### Coffee sorts and payment methods

**Coffee sorts**: what the kiosk offers – name, price and machine strokes.

![Coffee sorts](pictures/manual/en/dashboard-coffee-sorts.png)

**Payment methods**: PayPal needs the paypal.me name, PayPal Pool the pool's URL, WERO the
phone number; *Custom link* takes any address, optionally with `{amount}` for the amount owed.
These become the pay links and QR codes on kiosk and phone.

![Payment methods](pictures/manual/en/dashboard-payment-methods.png)

![Add payment method](pictures/manual/en/dashboard-payment-method-form.png)

### Members

The list of all members with their cards. Per member:

- **Edit**: nickname, UID, favourite coffee.
- **Add card**: the next card held to the kiosk becomes this member's – that's how someone gets
  a replacement card without ending up with a second account.
- **Revoke devices**: logs out all linked phones.
- **Mark as departed**: writes off the balance and blocks the cards; *Reactivate* undoes it.

![Members](pictures/manual/en/dashboard-members.png)

![Edit member](pictures/manual/en/dashboard-member-form.png)

**Merge members** helps when two accounts did end up existing: all orders, payments and cards
move to the account you keep, the other one is deleted.

![Merge members](pictures/manual/en/dashboard-merge.png)

### System

**System** shows the device's CPU, memory and disk.

![System](pictures/manual/en/dashboard-system.png)

**System time**: only needed when the real-time clock is missing or wrong. The new time is
also written to the clock within ten minutes.

![System time](pictures/manual/en/dashboard-system-time.png)

**Backup**: back up now, or download existing backups.

![Backup](pictures/manual/en/dashboard-backup.png)

**Update**: upload an update bundle (see [Updates](#updates)).

![Update](pictures/manual/en/dashboard-update.png)

#### Certificate

Shows which certificate is in use. You can upload **your own certificate** (PEM, with chain,
plus the unencrypted private key); it's live within seconds. CoffeePy checks that key and
certificate belong together and warns if it doesn't cover the device's addresses. A button
goes back to the self-signed certificate. The kiosk on the device always keeps the device's
own certificate – a bad upload can never lock it out.

![Certificate](pictures/manual/en/dashboard-certificate.png)

**Import legacy DB**: takes over the data of an old CoffeePy (V1) – a `coffee.db` or one of
its backups. The import keeps running in the background even if you close the page.

![Import](pictures/manual/en/dashboard-import.png)

**Audit log**: the last 500 changes made in the dashboard – who did what, when.

![Audit log](pictures/manual/en/dashboard-audit-log.png)

**Reset database** deletes **all** coffee data (members, cards, orders, payments, sorts,
payment methods, audit log). Dashboard logins stay. To confirm you have to type `DELETE` –
take a backup first!

![Reset database](pictures/manual/en/dashboard-reset.png)

For anything the dashboard doesn't cover there's the Django admin at `/admin/`.

---

## 7. Running and maintaining it

### Changing settings

Simply run the [provisioning tool](#the-provisioning-tool) again: it reads the existing
configuration, you change what you want, and it applies the change – the kiosk shows "We'll be
right back" meanwhile. Data, database password and certificate are kept.

### Backups

With backups on, CoffeePy backs up the database regularly (hourly by default, keeping the last
48) to the `COFFEEBAK` USB stick. Under **Backup** in the dashboard you can back up right away
and download backups.

### Updates

The device needs no internet for updates:

1. On a PC with internet, build an update bundle:
   `./scripts/build_update_bundle.sh linux/arm64 coffeepy-update.tar` (`linux/arm/v7` for a
   32-bit system).
2. Upload it in the dashboard under **Update** (via USB stick on a laptop in the device's WiFi,
   or directly).
3. The device applies it by itself. Meanwhile the kiosk shows "We'll be right back" with a
   progress bar, the update page shows the log.

An update also works from a PC through the provisioning tool.

### Language

You pick the default language during setup. Every device can switch for itself: the kiosk
with its **DE/EN** button, phones and PCs at the top right of the portal and at the bottom left
of the dashboard (or below the login form). Every device remembers its own choice.

---

## 8. When something goes wrong

| Problem | Solution |
|---|---|
| The browser warns about the site. | Install the device certificate once (see [above](#installing-the-certificate)). |
| **Bad Request (400)** in the browser. | The device doesn't know the address you're using. Use the address from setup (`192.168.4.1`, `coffeepy.local`), or run the provisioning tool over the new address – it adds it. |
| After switching on, the screen is black, then white, for a minute. | Normal: the Pi starts first, then the browser. The kiosk appears after about a minute. |
| *Card reader not available* | Check the reader's cable and restart the device. |
| A card isn't recognised. | Hold it flat and still; a card marked as departed is turned away. |
| The time is wrong. | Replace the real-time clock's battery, then correct the time under **System time** in the dashboard. |
| "We'll be right back" doesn't go away. | Is an update running? The dashboard's **Update** page shows its log. A status older than two hours is ignored; when in doubt, restart the device. |
| The phone can't reach the dashboard. | Is it connected to the device's WiFi? Turn mobile data off briefly – some phones leave a WiFi without internet otherwise. |
