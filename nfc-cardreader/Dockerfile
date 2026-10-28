FROM python:3.14-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        swig \
        libpcsclite-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        pcscd \
        libpcsclite1 \
        libccid \
        libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Let escape commands through to the reader itself (no card needed) - how bridge.py turns
# the reader's beep off. libccid ships with them refused (ifdDriverOptions 0x0000). A layer
# of its own, so a change here doesn't redo the apt layer above - which needs the internet a
# device in service doesn't have.
RUN sed -i '/<key>ifdDriverOptions<\/key>/{n;s|<string>0x0000</string>|<string>0x0001</string>|}' \
        /usr/lib/pcsc/drivers/ifd-ccid.bundle/Contents/Info.plist \
    && grep -A1 '<key>ifdDriverOptions</key>' /usr/lib/pcsc/drivers/ifd-ccid.bundle/Contents/Info.plist | grep -q 0x0001

COPY --from=builder /install /usr/local
# The base image's pip lags behind its fixes (the CI audit fails on that) - a newer one.
RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app
COPY bridge.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV MQTT_HOST=localhost \
    MQTT_PORT=1883 \
    MQTT_TOPIC=nfc/acr122u \
    MQTT_CLIENT_ID=acr122u-bridge \
    LOG_LEVEL=INFO

ENTRYPOINT ["/entrypoint.sh"]
