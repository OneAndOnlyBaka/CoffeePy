# coffee-machine - the coffee machine bridge

Connects one coffee machine to MQTT, the way `nfc-cardreader/` connects the card reader. CoffeePy's
`mqtt-listener` is one client of it; Home Assistant can be another (below) - with CoffeePy or
without it. Without a configured machine the container idles and reports `disabled`.

Today's driver: **JURA over Bluetooth LE** (`jura_ble`) - the machine's BlueFrog, built in or the
Smart Connect dongle (72167) on its service port. Tried on an E6 (EF532M V02.03, Smart Connect
BlueFrog V05.08F). A USB FTDI cable on the service port is a later driver of its own; another
maker's machine is either a driver in `drivers/` or a bridge of its own that speaks the same topics.

## Running it

In CoffeePy's stack it is the `machine-bridge` service (`docker-compose.yml`) and is configured from
the dashboard (Coffee machine). Bluetooth goes through the host's BlueZ: `/run/dbus` is mounted in,
the container itself needs no privileges. The host's Bluetooth is off on a CoffeePy device until the
dashboard switches it on (install/storage_helper.py).

On its own, e.g. next to Home Assistant:

```bash
docker build -t coffee-machine .
docker run -d --restart unless-stopped -v /run/dbus:/run/dbus:ro \
  -e MQTT_HOST=homeassistant.local -e MQTT_USER=... -e MQTT_PASSWORD=... \
  -e MACHINE_DRIVER=jura_ble -e MACHINE_ADDRESS=F4:57:52:42:0C:04 \
  -e HA_DISCOVERY=true coffee-machine
```

| Variable | Default | |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD` | `localhost`, `1883` | the broker |
| `MACHINE_TOPIC` | `coffeemachine` | topic prefix |
| `MACHINE_DRIVER` | `none` | `jura_ble` |
| `MACHINE_ADDRESS` | | the machine's Bluetooth address (find it with a `scan` command) |
| `BLOCK_MANUAL` | `false` | lock the machine's keys while connected |
| `HA_DISCOVERY` | `false` | publish Home Assistant MQTT discovery |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | |
| `HA_BREW` | `false` | buttons in Home Assistant that brew - past any tally |
| `MACHINE_SIMULATE` | `false` | a pretend E6 (`drivers/simulator.py`) whatever the config says - for trying the kiosk without a machine; `{"action": "simulate", "alerts": [...]}` on `cmd` sets its messages |

A retained `<prefix>/config` overrides driver, address and `block_manual` (that is how CoffeePy
configures it).

## MQTT contract

All payloads JSON; `<prefix>` defaults to `coffeemachine`.

| Topic | From | Retained | Payload |
|---|---|---|---|
| `status` | bridge | yes (also last will) | `online` / `offline` / `disabled` (plain text) |
| `config` | app | yes | `{"driver", "address", "block_manual"}` |
| `cmd` | app | no | `{"id", "action": "scan" \| "brew" \| "refresh", "product", "options", "source", "request_ids"}` |
| `scan` | bridge | no | `{"id", "devices": [{"address", "name", "rssi", "article", "model"}], "error"}` |
| `state` | bridge | yes | `{"connected", "model", "article", "firmware", "alerts": [...], "blocking": [...], "locked", "progress": {"phase", "product", "percent", "water_ml", "strength"}, "error", "driver", "address", "block_manual", "updated"}` |
| `event` | bridge | no | `{"type", "request_id", "source", "product", "water_ml", "strength", "reason", "cup", "of", "time"}` |
| `counters` | bridge | yes | `{"total", "products": {"<code>": n}, "maintenance": {"counters": {...}, "percent": {...}}, "taken"}` |
| `products` | bridge | yes | `{"article", "model", "products": [{"code", "name", "strokes", "water_ml", "strength", "brewable", "recipe"}]}` |

- `progress.phase`: `idle`, `heating`, `starting`, `dispensing`, `done`.
- `event.type`: `brew_started`, `brew_done`, `brew_failed` (`reason`: `busy`, `blocked`, `not started`,
  `stalled`, `disconnected`, ...). `source`: `app` (asked for over `cmd`), `free` (asked for, no tally),
  `ha`, `manual` (started at the machine).
- `brew` options: `water_ml`, `strength`, `temperature` (0 low, 1 normal, 2 high); without them the
  machine definition's defaults. A product without a definition (the E6's hot water) needs `water_ml`.
- `recipe` of a product: what a brew may ask for - `{"strength": {"min", "max", "default"}, "water": {"min",
  "max", "step", "default"}, "temperature": {"options": [...], "default"}}`, each only where the product
  has it; `{}` for one that takes nothing (milk foam), missing for one without a definition.
- **Several cups in a row:** `request_ids` (one per cup) makes a `brew` that many cups of the same recipe,
  into one big cup or a jug - CoffeePy's "my coffees". The next starts once the machine is idle and says
  `coffee_ready` again (or 30 s later, for a model that doesn't); each cup has its own `brew_started`/
  `brew_done` with `cup` and `of`. A cup that fails, a blocking message that stays for 2 minutes or a lost
  connection ends the chain: the cups not made yet get `brew_failed`. Home Assistant's buttons brew one.

## What the JURA does (measured, E6)

- The key in the advertisement **changes** - it is read at every connect. Some advertisements lack the
  manufacturer data; the bridge scans again.
- No command cancels a running product. The **keys can be locked** (Barista mode): the machine's own
  keys do nothing, products started over Bluetooth still run. The lock lives with the connection:
  lost, the machine drops it by itself within ~40 s - a crashed bridge never leaves it locked.
- A product chosen at the machine shows in the progress about a second after the key press, with its
  recipe (strength, water in 5 ml steps, temperature) - that is how a manual brew is told apart and
  how much water it took.
- Statistics: py-jura 0.2.1 waits for the wrong "ready" byte on this firmware (the E6 answers 0x0e).
- The UART tunnel (5a401623/24/25) carries the machine's serial bus; this bridge only ever reads the
  documented characteristics. A wrongly framed write to the tunnel made the dongle change its key.

## Home Assistant

With `HA_DISCOVERY=true` the bridge announces one device with binary sensors for what someone has to
do at the machine (water, grounds, drip tray, beans, error, keys locked), sensors for the counters per
product and the maintenance due, and the brewing phase. `availability` is `<prefix>/status`.
In CoffeePy's stack the broker is internal; for Home Assistant on another host either point this
bridge at Home Assistant's broker or bridge the two mosquittos.
