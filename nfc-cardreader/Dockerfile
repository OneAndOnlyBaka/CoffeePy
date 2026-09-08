FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        swig \
        libpcsclite-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        pcscd \
        libpcsclite1 \
        libccid \
        libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

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
