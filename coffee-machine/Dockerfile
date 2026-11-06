FROM python:3.14-slim AS builder

# dbus-fast (bleak's D-Bus client) has no wheel for every architecture the devices run; it builds
# its C extension here then, or falls back to pure Python where that fails.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.14-slim

COPY --from=builder /install /usr/local
# The base image's pip lags behind its fixes (the CI audit fails on that) - a newer one.
RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app
COPY bridge.py codec.py policy.py ha.py ./
COPY drivers/ drivers/

# Bluetooth goes through the host's BlueZ: /run/dbus is mounted in (docker-compose.yml), nothing
# here touches the adapter itself, so the container needs no privileges.
ENV MQTT_HOST=localhost \
    MQTT_PORT=1883 \
    MACHINE_TOPIC=coffeemachine \
    MACHINE_DRIVER=none \
    MACHINE_SIMULATE=false \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

CMD ["python", "bridge.py"]
