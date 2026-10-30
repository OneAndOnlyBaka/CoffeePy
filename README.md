![CoffeePy](docs/pictures/coffeepy.png)

# CoffeePy

**The tally sheet next to your office coffee machine - as a touchscreen that knows everyone by
their card.**

Tap your card, take your coffee, done. CoffeePy keeps count of who drank what, who paid, and
who still owes - so nobody has to keep a paper list, chase colleagues or do the maths at the end
of the month. It runs on a Raspberry Pi in a small 3D-printed housing next to the machine,
works completely offline, and everyone checks their own balance on their phone.

<table>
  <tr>
    <td><img src="docs/pictures/manual/en/kiosk-order.png" alt="Ordering on the kiosk"></td>
    <td><img src="docs/pictures/manual/en/kiosk-ranking-alltime.png" alt="The all-time ranking"></td>
  </tr>
  <tr>
    <td align="center"><em>Tap your card - your favourite is already selected</em></td>
    <td align="center"><em>An optional podium for the office's most loyal drinkers</em></td>
  </tr>
  <tr>
    <td><img src="docs/pictures/manual/en/dashboard-statistics.png" alt="Statistics in the dashboard"></td>
    <td><img src="docs/pictures/manual/en/kiosk-pay.png" alt="Paying on the kiosk"></td>
  </tr>
  <tr>
    <td align="center"><em>A dashboard for whoever buys the beans</em></td>
    <td align="center"><em>Pay by cash, PayPal or a QR code with the amount filled in</em></td>
  </tr>
</table>

## Why CoffeePy

### At the machine
- **One tap per coffee.** Any MIFARE card or key fob works - many company and transit cards
  already do. Your favourite coffee is preselected; after 20 seconds it is booked by itself.
- **A jug counts as two cups.** Every coffee sort has its price and its number of machine
  strokes, so a double or a jug is counted - and charged - for what it is.
- **Pay where you drink.** The kiosk shows what you owe and offers QR codes for PayPal or any
  payment link, with the amount already filled in. A friendly reminder appears when last
  month is still open.
- **A little fun, if you want it.** An all-time and a monthly podium, a "walk of shame" for
  those who owe, and thank-you animations that notice when it's your fifth cup today. Both
  boards are off by default - a workplace decision, not a software one.

### On your phone
- **Scan a QR code on the kiosk once** and your phone stays linked for six months - balance,
  payments, your cards and how your coffee habits compare with everyone else's.
- **No app, no account, no password.** A one-time link, a secure session, and a list of linked
  devices you can revoke at any time.

### For whoever runs it
- **A dashboard for a phone or a PC:** members, corrections, coffee sorts and prices,
  payment methods, statistics, a pillory that adds up what is open, CSV exports.
- **Everything is logged.** Every change - on the kiosk, the phone or the dashboard - writes an
  audit entry with who, what and from where.
- **Two roles:** admins for everything, managers for the day-to-day bookkeeping. Login rules,
  lockouts and password policies are set in the dashboard.

### Built for a corner of the office
- **Fully offline.** No cloud, no CDN, no internet - the Pi can even run its own WiFi access
  point for the phones. HTTPS throughout, with a device CA phones install once.
- **Backups on a USB stick**, automatic, verified, optionally encrypted, restorable from any
  older version.
- **Signed offline updates** that put themselves back if the new version doesn't come up -
  even after a power cut in the middle of one.
- **German and English**, switchable on every screen.
- **A clock that keeps time** without internet (DS3231 RTC), a night rest for the screen,
  and a watchdog that restarts a stuck kiosk.
- **Set up from your PC** with one guided tool: `scripts/provision.sh` asks what it needs and
  installs the whole device over SSH.

## What you need

A Raspberry Pi 3 or newer with 1 GB RAM, a 1024×600 touchscreen, an ACR122U NFC reader, a
DS3231 clock module and, recommended, a USB stick for backups - the
[manual](docs/MANUAL.en.md#1-what-you-need) has the full list with what the device actually
uses. The housing is printable:

![The 3D-printed housing](docs/pictures/housing.jpg)

## Get started

| | |
|---|---|
| **Try it or install it** | [Quickstart](docs/QUICKSTART.md) - on a PC in four commands, on a Pi with the provisioning tool |
| **Use it** | The manual in [English](docs/MANUAL.en.md) or [German](docs/MANUAL.de.md) - every screen, with screenshots |
| **Understand it** | [Architecture](docs/ARCHITECTURE.md) (system and software) and [how the numbers are calculated](docs/CALCULATIONS.md) |
| **Trust it** | [Security mechanisms](docs/SECURITY.md) and [running it in line with the GDPR](docs/PRIVACY.md) |
| **Change it** | [Development](docs/QUICKSTART.md#development) and [CLAUDE.md](CLAUDE.md) |
| **Build and release it** | [docs/CI.md](docs/CI.md) |

Coming from CoffeePy V1? Your data comes along: [Moving over from V1](docs/QUICKSTART.md#coming-from-v1).

## Project status

CoffeePy is built and run by one person for one office. That is fine for its own use, but
whoever runs it elsewhere should be able to maintain it themselves - it is MIT-licensed, and
made to be maintainable by someone new: [CLAUDE.md](CLAUDE.md) writes down what is not obvious
and what already went wrong once, the tests run on SQLite and PostgreSQL with the backup and
update code at full coverage, every push is audited for known vulnerabilities, Dependabot
proposes the updates, and every release carries SBOMs of what a device runs
([docs/CI.md](docs/CI.md)). Issues and pull requests are welcome.

## Disclaimer

> **This is not a point-of-sale system.** CoffeePy replaces the paper tally sheet
> next to a shared coffee machine, nothing more. It does not sell anything and is not a cash
> register: no receipts, no tax or VAT handling, no tamper-proof fiscal records (no TSE as the
> German KassenSichV requires, no GoBD-compliant bookkeeping), and it doesn't process payments –
> a payment entered on the kiosk or the phone is a note that someone says they paid, and the
> payment links only open the member's own app. Balances are an informal tally among the people
> sharing the machine. If coffee is sold commercially, that needs a real POS system. CoffeePy
> is provided as is, without warranty – see [LICENSE](LICENSE).
>
> **No liability.** As far as the law allows, the authors accept no liability for damage of any
> kind arising from installing or using CoffeePy – among others lost or wrong data and
> balances, money owed or paid, disputes among members, failed backups or updates, damage to
> the Raspberry Pi, the reader, the coffee machine or anything connected, and loss of data on
> the SD card or the backup stick. Whoever runs it is responsible for checking the balances,
> keeping backups and operating the hardware safely. This does not limit liability for intent,
> gross negligence or injury to life, body or health where the law forbids that.

## Credits

The kiosk's nav icons (`static/kiosk/img/nav/`) and its "apply your card" animations
(`static/kiosk/img/wait/`) are drawn by `scripts/make_nav_icons.py` and
`scripts/make_wait_gifs.py` - change them there and re-run. V1's photographic nav icons
from [iconarchive.com](https://iconarchive.com/) were replaced to match the flat design.

MIT licensed - see [LICENSE](LICENSE).
