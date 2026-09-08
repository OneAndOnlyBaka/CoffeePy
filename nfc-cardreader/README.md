# ACR122U → MQTT Bridge

Docker image that reads an ACS ACR122U USB NFC reader via PC/SC (`pcscd` + `libccid`)
and publishes card events as MQTT messages. Ships as a multi-arch image for x86_64
and ARM (arm64/armv7), e.g. for a Raspberry Pi.

## Contents

- [Dockerfile](Dockerfile) — multi-stage build (`python:3.12-slim`, Debian-based)
- [bridge.py](bridge.py) — reads the card UID over PC/SC, publishes to MQTT
- [entrypoint.sh](entrypoint.sh) — starts `pcscd` and the bridge inside the container
- [docker-compose.yml](docker-compose.yml) — example setup against an external MQTT broker
- [docker-compose.test.yml](docker-compose.test.yml) — test setup including its own Mosquitto broker + subscriber
- [udev/99-acr122u.rules](udev/99-acr122u.rules) — udev rule for USB permissions (only needed for rootless Podman)

## MQTT payload

Topic `<TOPIC>` (default: `nfc/acr122u`):

```json
{
  "event": "card_inserted",
  "uid": "04A1B2C3",
  "uid_length": 4,
  "atr": "3B 8F 80 01 80 4F 0C A0 00 00 03 06 03 00 01 00 00 00 00 6A",
  "card_type": "MIFARE Classic 1K",
  "atqa": "0004",
  "sak": "08",
  "ndef": [{"type": "U", "uri": "https://example.com"}],
  "dwell_time_ms": null,
  "reader": "ACS ACR122U PICC Interface 00 00",
  "timestamp": 1753...
}
```

Field details:

- `card_type` — decoded from the ATR historical bytes (PC/SC Part 3 registry). `null` if not recognized.
- `atqa` / `sak` — read via the ACR122U's own "Direct Transmit" pseudo-APDU (PN532 `InListPassiveTarget`). `null` if the card/reader doesn't provide it.
- `ndef` — list of parsed NDEF records (Text/URI records in plain text, everything else as `payload_hex`), only for NDEF-formatted Type 2 tags (e.g. NTAG21x, MIFARE Ultralight). `null` if no NDEF was found or the card doesn't support simple block reads (e.g. MIFARE Classic without auth, DESFire).
- `dwell_time_ms` — only set on the `card_removed` event: how long the card was present between `card_inserted` and `card_removed`.

Additionally, a retained status topic `<TOPIC>/status` (`online`/`offline`, with last will).

**Note:** all extra fields are best-effort and never break the flow on failure —
for unsupported cards/commands they simply stay `null`.

## Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `MQTT_HOST` | `localhost` | Broker address |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USER` / `MQTT_PASSWORD` | – | Optional credentials |
| `MQTT_TOPIC` | `nfc/acr122u` | Base topic |
| `MQTT_CLIENT_ID` | `acr122u-bridge` | MQTT client ID |
| `MQTT_TLS` | `false` | Enable TLS |
| `MQTT_QOS` | `0` | QoS for events |
| `MQTT_RETAIN` | `false` | Publish events retained |
| `LOG_LEVEL` | `INFO` | Python log level |
| `PCSCD_DEBUG` | `false` | `pcscd` debug logging |

## Build (multi-arch)

```bash
docker buildx create --use   # once
docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t youruser/acr122u-mqtt-bridge:latest --push .
```

Test locally for a single platform:

```bash
docker buildx build --platform linux/amd64 -t acr122u-mqtt-bridge:latest --load .
```

---

## Setup: Fedora (Podman, rootless)

On Fedora, Podman runs **rootless** by default and Fedora has **SELinux enforcing** —
both require a few extra steps that are normally unnecessary under Docker
(e.g. on Raspbian).

### 1. Get competing kernel/host processes out of the way

The ACR122U contains a PN532 chip internally. The Linux kernel has its own NFC
subsystem (`pn533_usb`, `pn533`, `nfc`) that automatically recognizes the reader and
claims the USB interface at the kernel level — after that, no userspace program
(neither host nor container `pcscd`) can get access anymore (`LIBUSB_ERROR_BUSY`).

```bash
lsmod | grep -E 'pn533|nfc'
```

If there are hits: unload the modules and blacklist them permanently.

```bash
sudo modprobe -r pn533_usb pn533 nfc

echo "blacklist pn533_usb
blacklist pn533
blacklist nfc" | sudo tee /etc/modprobe.d/blacklist-acr122u-kernel-nfc.conf
```

Additionally, many Fedora desktops run their own `pcscd` (socket-activated), which
also claims the interface. If you want to use the reader exclusively through the
container:

```bash
sudo systemctl stop pcscd.service pcscd.socket
sudo systemctl mask pcscd.service pcscd.socket
```

`mask` matters here — `disable` alone does not prevent socket activation.

### 2. USB permissions for rootless Podman

Rootless Podman maps the container root to an unprivileged host user. Without
further changes, the container can't open the USB device node (owned by
`root:pcscd`) — `LIBUSB_ERROR_ACCESS`. Fix via a udev rule:

```bash
sudo cp udev/99-acr122u.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Afterwards unplug and replug the reader once (the rule reliably applies only on
the `add` event).

### 3. SELinux boolean for device passthrough

Even with correct Unix permissions, SELinux on Fedora blocks container access to
host devices by default:

```bash
sudo setsebool -P container_use_devices 1
```

File bind mounts (e.g. the Mosquitto config in [docker-compose.test.yml](docker-compose.test.yml))
additionally need an SELinux label on the volume entry: `:ro,Z` or `:Z`.

### 4. Start it

```bash
podman compose -f docker-compose.test.yml up --build
```

On "device not found" errors after a replug/module unbind: the bus/device number
often changes, so old containers need to be recreated entirely:

```bash
podman compose -f docker-compose.test.yml down
podman compose -f docker-compose.test.yml up --build
```

---

## Setup: Raspberry Pi OS / Raspbian (Docker)

Raspberry Pi OS ships Docker (not Podman) and runs **rootful** without SELinux —
most of the Fedora-specific hurdles above don't apply. Only the generic USB/kernel
conflicts remain:

### 1. Check kernel NFC modules

Just like on Fedora, the Raspberry Pi kernel may have loaded `pn533_usb`/`pn533`/`nfc`
and claimed the interface before the container gets a chance:

```bash
lsmod | grep -E 'pn533|nfc'
```

If present, handle the same way as above:

```bash
sudo modprobe -r pn533_usb pn533 nfc
echo "blacklist pn533_usb
blacklist pn533
blacklist nfc" | sudo tee /etc/modprobe.d/blacklist-acr122u-kernel-nfc.conf
```

### 2. Check for a host `pcscd`

If `pcsc-lite`/`pcscd` is already installed natively on the Pi (e.g. via
`apt install pcscd`), stop it so two `pcscd` instances don't compete for the same
reader:

```bash
sudo systemctl stop pcscd pcscd.socket
sudo systemctl disable pcscd pcscd.socket
```

(`disable` is usually enough here, as long as no other local PC/SC client
re-triggers socket activation.)

### 3. Start the container

Docker runs rootful, so a plain `--device` is enough — no udev rule and no
SELinux boolean needed:

```bash
docker compose -f docker-compose.test.yml up --build
```

If the bus/device number differs or multiple USB readers are attached, mount
`/dev/bus/usb` (instead of a single device path) in
[docker-compose.test.yml](docker-compose.test.yml) / [docker-compose.yml](docker-compose.yml),
as already configured — that automatically covers new bus/device numbers after a
replug.

### 4. Build the image directly on the Pi (alternative to a multi-arch push)

Instead of a pushed multi-arch image, the image can also be built natively right
on the Pi (slower, but no registry needed):

```bash
docker compose build
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Unable to open config file` (Mosquitto) | SELinux blocks the bind mount | Label the volume with `:Z` |
| `Rejected unauthorized PC/SC client` / `polkit_authority_get_sync failed` | `pcscd` requires Polkit authorization, no D-Bus in the container | `pcscd --disable-polkit` (already in [entrypoint.sh](entrypoint.sh)) |
| `LIBUSB_ERROR_ACCESS` | Rootless Podman: container user has no permission on the device node | Install the udev rule ([udev/99-acr122u.rules](udev/99-acr122u.rules)) |
| `LIBUSB_ERROR_ACCESS` despite `0666` permissions | SELinux blocks device access generally | `sudo setsebool -P container_use_devices 1` |
| `LIBUSB_ERROR_BUSY` when claiming the interface | Kernel NFC driver (`pn533_usb`/`pn533`/`nfc`) or host `pcscd` holds the interface | Blacklist the modules, stop/mask host `pcscd` |
| `crun: cannot stat /dev/bus/usb/...: No such file or directory` | Old container points at a now-invalid bus/device number (after a replug) | `down` + `up` instead of just `up`, so the device mapping gets re-resolved |
| `no container with name or ID "mqtt-client" found` | Container was never created (build failed earlier) | Check the `up --build` output for errors before calling `logs` |
