# Two stages so the runtime image never carries a toolchain. It earns its keep on the
# Raspberry Pi: the image is 32-bit armhf, and neither psutil nor psycopg's binary build
# publishes an armv7 wheel, so pip has to compile psutil from source - which the runtime
# base (python:3.14-slim, no compiler) cannot do. On amd64/arm64 pip just finds wheels
# here and this stage costs almost nothing.
FROM python:3.14-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.14-slim

# postgresql-client: pg_dump for the backup service, and libpq5 - which is also what the
# pure-Python psycopg used on armv7 loads at runtime.
# util-linux-extra, not util-linux, is what ships hwclock since Debian bookworm; without
# it dashboard.system_clock's `hwclock -w` is a silent no-op (it tolerates failure). It
# still only writes the RTC if /dev/rtc0 is mapped into this container - the guaranteed
# path is the host's coffeepy-rtc-sync.timer (see install/rtc_sync.sh).
# openssl: dashboard/tls.py creates and checks Caddy's certificates with it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssl \
        postgresql-client \
        util-linux \
        util-linux-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
COPY --from=builder /wheels /wheels
# The base image's pip lags behind its fixes (the CI audit fails on that) - a newer one first.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .

RUN python manage.py collectstatic --noinput

# Runs as root (not dropped to an unprivileged user) so the `web` service can actually use
# the CAP_SYS_TIME capability granted in docker-compose.yml for /dashboard/system-time/.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
