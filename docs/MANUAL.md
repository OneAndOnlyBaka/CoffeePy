# CoffeePy manual

CoffeePy is a self-service tally for a shared coffee machine: members tap an NFC card to
order, the dashboard tracks who owes what. This is CoffeePy **V2** - a rework of the
original Tkinter-based CoffeePy as a Django web app (kiosk + dashboard both run in the
browser now), keeping the same day-to-day usage. For installation and technical details,
see the main [README](../README.md); this document covers what to buy and how to use it.

Upgrading from V1? Install V2 fresh on the device and import your old `coffee.db` from
the dashboard ("Import legacy DB", below) - the old software is not upgraded in place.

## Hardware to buy

| Part | Notes |
|---|---|
| Raspberry Pi | **3 Model B or newer** (3, 3+, 4, 5, Zero 2 W, CM3/CM4). Install onto Raspberry Pi OS **Lite** with [install/install.sh](../install/install.sh); the 32-bit OS keeps a 1GB Pi 3 comfortable, a **Pi 4 (2GB+)** has more headroom, and a Pi 5 needs the 64-bit one. |
| MicroSD card | 16GB+, A1/A2-rated for reliability - this is the boot/root disk. |
| Power supply | The official USB-C (Pi 4/5) or micro-USB (Pi 3) supply for your board - underpowered generic supplies are a common source of flaky USB/touchscreen behaviour. |
| Touchscreen | 1024×600 (e.g. [Adafruit #2396](https://www.adafruit.com/product/2396), the original CoffeePy's recommendation) works well. The kiosk has also been tested this rework against the smaller, cheaper **official 7" Raspberry Pi touchscreen (800×480)** - the layout is responsive and fits either. |
| NFC reader | Advanced Card Systems **ACR122U** - the only reader `nfc-cardreader/` currently supports. |
| RTC module | **DS3231** I²C module, so the Pi keeps correct time fully offline (no NTP). Wire it to the Pi's I²C-1 header: VCC→pin 1 (3.3V), GND→pin 6, SDA→pin 3, SCL→pin 5. |
| NFC cards/tags | Any MIFARE Classic/Ultralight (or similar ISO14443A) card or key fob per member - what most access-card/transit cards already use. |
| USB stick (optional) | For offline Postgres backups - format it `ext4` and label it `COFFEEBAK` (`mkfs.ext4 -L COFFEEBAK /dev/sdX1`); the installer's udev rule auto-mounts it at `/mnt/coffeepy-backup`. |

### Housing

![](../doc/pictures/housing.jpg)

A 3D-printable desk housing for the Pi, the 1024x600 screen and the ACR122U is in
[housing/](../housing/) - unchanged from V1, since V2 uses the same hardware. Print the
`LCD_*` screen frame and the matching `Card_*` reader holder from `housing/3D/` in the
orientation you want (`_H` horizontal, `_V` vertical, `_V_T1`/`_V_T2` if your printer
needs the vertical frame split in two), and the labels in `housing/labels/` finish it off.
Any other enclosure works just as well - nothing in the software depends on it.

## Using the kiosk

The touchscreen has four sections, picked from the icons on the left. Apart from
**Ranking**, every section needs a card tap to do anything - tapping the same card again
while a section is already showing you logs back in there.

### Order (coffee cup icon)

The default screen: **"Apply your card"**, with a live clock (useful for noticing a
flat RTC battery - if the date/time is visibly wrong, replace the DS3231's coin cell).

Tap a known card → greets you by name and shows the coffee list. Pick a sort (your
favourite, set in Settings, is pre-selected), then either wait - it checks out
automatically after 20 seconds - or tap **✓ Order now** to skip the wait, or **✕
Cancel** to back out without ordering. Picking a different sort from the default stops
the auto-checkout countdown, so you always get a chance to confirm your actual choice.

Tapping an unrecognised card sends you to **Settings** instead, to set up a nickname
first (see below).

### Settings (gear icon)

Tap your card to edit your **nickname** and **favourite coffee**. A brand-new card shows
"Welcome, new card!"; by default the nickname is just the card's own ID until you set
one. Type directly (a physical keyboard works normally) or tap **⌨ Keyboard** for an
on-screen one. Tap **Save** to store it and log out - if you don't touch anything, it
logs out on its own after 20 seconds (typing or opening the on-screen keyboard keeps
that timer from running out while you're mid-edit).

### Pay & Stats (books icon)

Tap your card to see your **CCT**/**CCM** (cup count, total / this month) and current
**balance**. A negative balance pre-fills the payment field with exactly what you owe -
adjust it if you're paying a different amount, then tap **Pay**. Same 20-second
auto-logout as Settings.

### Ranking (trophy icon)

No card needed. Tap **Next ▸** to cycle through three views: all-time top 7, this
month's top 7, and the **deposit pillory** - members with a negative balance, so
everyone can see who still owes money.

### WLAN Settings (the round 📡 button)

Only shown on the idle "Apply your card" screen. Opens a popup with two QR codes: one to
join the Pi's own WiFi (once it's running in standalone AP mode), one to open the
dashboard from a phone or PC - handy since the kiosk touchscreen itself has no physical
keyboard for admin tasks.

## Using the dashboard

Open `/dashboard/` (or scan the QR code above) and log in. A few pages need a
**superuser** account specifically (backup, update, import, system time); everything
else works for any logged-in staff account.

- **Statistics** - monthly order/payment totals, coffee-sort distribution, price trend,
  and when coffee actually gets drawn: cups per hour of the day and per weekday, both
  all-time. Those two count machine strokes (a jug is two cups) and use local time, and
  each chart's title names its busiest slot.
- **Pillory** - the same negative-balance list the kiosk's ranking screen shows.
- **Coffee sorts** - add/edit/delete the drinks selectable on the kiosk (name, price,
  machine strokes).
- **Members** - add/edit/delete members, and two things worth knowing about:
  - **Add card**: if a member gets a second/replacement card, this arms the kiosk to
    link the *next* card tapped there as an additional card on their account - much
    easier than typing a card's numeric ID by hand. Shows live on the kiosk screen
    ("Assigning card to account ..."), 60-second timeout. A member can have any number
    of these, so replacing a lost card again later doesn't cause problems.
  - **Merge members**: for when someone already ended up with two separate accounts
    instead (old card broke, a new tap started a fresh account rather than using "Add
    card" first) - picks which account to keep, moves the other's order/payment history
    and any cards it already had onto it, links its own card too, and removes the
    duplicate. Whichever of the two accounts was more recently active keeps its nickname,
    regardless of which one you picked as "keep".
- **Backup** (superuser) - trigger a Postgres dump immediately, or download existing
  ones (see `BACKUP_ENABLED`/`BACKUP_HOST_PATH` in `.env`).
- **Update** (superuser) - upload an offline update bundle (see the main README's
  "Offline updates" section).
- **Import legacy DB** (superuser) - import an old CoffeePy `coffee.db`/`coffeeN.db`. The
  import runs in the background and the page shows upload and import progress; it keeps
  running if you close the page, and reopening it picks the progress back up. The optional
  "clear existing data first" checkbox deletes members, cards, orders, payments and coffee
  sorts before importing (payment methods and the audit log are kept) - it happens in the
  import's own transaction, so a dump that turns out to be unreadable deletes nothing.
- **Reset database** (superuser) - deletes *all* coffee data: members, cards, linked
  phones, orders, payments, coffee sorts, payment methods, NFC scans and the audit log.
  Dashboard logins are kept, so you don't lock yourself out. Requires typing `DELETE` to
  confirm and cannot be undone - take a backup first.
- **System time** (superuser) - manual clock fallback if the DS3231 drifts or isn't
  fitted. A correction made here is written back to the RTC within ten minutes (the
  `coffeepy-rtc-sync` timer), so it survives the next power cut; before that timer
  existed, the RTC quietly restored the old time at the next boot.

Coffee sorts, members, orders and payments are also reachable via the Django admin at
`/admin/` (superuser) as a fallback for anything the dashboard doesn't cover.
