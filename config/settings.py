"""
Django settings for the CoffeePy project.
"""

import json
import os
import tempfile
from pathlib import Path

from coffee.network import detect_local_ip

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")

DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Hostname/IP other devices (a phone scanning a kiosk QR code) should use to reach this
# app - see coffee/network.py. Explicit env var wins; otherwise best-effort autodetect.
# Either way, it must be in ALLOWED_HOSTS or Django would just reject those requests.
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "").strip()
_public_host = PUBLIC_HOST or detect_local_ip()

# Every name the network certificate is issued for (dashboard/tls.py) - the address QR
# codes use, plus the Pi's AP address and the name install/dnsmasq.conf.template gives it.
TLS_HOSTNAMES = list(dict.fromkeys(
    [_public_host] + [h.strip() for h in os.environ.get("TLS_EXTRA_HOSTNAMES", "192.168.4.1,coffeepy.local").split(",")
                      if h.strip()]
))
for _host in TLS_HOSTNAMES:
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)

# Certificates and the Caddyfile (see dashboard/tls.py); docker-compose.yml mounts
# ./data/tls there.
TLS_DIR = Path(os.environ.get("TLS_DIR", BASE_DIR / "data" / "tls"))

# Only Caddy can reach the app (docker-compose.yml publishes no port of web's own), and it
# sets this header itself - so is_secure() is true for every request through it.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Off by default only so a plain `runserver` over http still keeps a login.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = LANGUAGE_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)


# No django.contrib.admin: dashboard users are managed in the dashboard itself (dashboard
# "Dashboard users"), and corrections to orders and payments happen under "Bookings" -
# with an audit row each, which the admin never wrote.
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "coffee",
    "kiosk",
    "dashboard",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.language.DeviceLanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "dashboard.security.DashboardSecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.version",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


if env_bool("DJANGO_TEST_SQLITE", False):
    # Local dev/test convenience when no Postgres is reachable - never set in compose.
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "coffeepy"),
            "USER": os.environ.get("POSTGRES_USER", "coffeepy"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "coffeepy"),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            # The kiosk polls twice a second. A fresh connection per request means a forked
            # Postgres backend plus a SCRAM handshake each time - measured on a Pi 3, that was
            # ~85ms of a ~117ms poll and capped the whole app at ~10 requests/s.
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    # Length and character kinds after the BSI's recommendation (replaces Django's plain
    # minimum length) - see dashboard/security.py.
    {"NAME": "dashboard.security.BSIPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Every screen is available in both; which one a device shows is its own choice (the
# kiosk's language button, the switch in dashboard and portal - a cookie per device, see
# config/language.py). Until a device chooses, it gets the default picked when the Pi was
# set up (scripts/provision.sh) - never the browser's own language.
LANGUAGES = [("de", "Deutsch"), ("en", "English")]
LANGUAGE_CODE = os.environ.get("DJANGO_LANGUAGE_CODE", "en").strip() or "en"
if LANGUAGE_CODE not in dict(LANGUAGES):
    LANGUAGE_CODE = "en"
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_AGE = 10 * 365 * 24 * 3600
LANGUAGE_COOKIE_SAMESITE = "Lax"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "dashboard:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "dashboard:login"

# Kiosk session/auto-logout timings (seconds), matching the legacy Tkinter app.
KIOSK_ORDER_AUTO_CHECKOUT_SECONDS = 20
KIOSK_AUTO_LOGOUT_SECONDS = 20

# Where uploaded update bundles are written for the host-level watcher to pick up.
# What the watcher reports back (state.json, installed.json, previous.json, pre-update/).
UPDATE_DIR = Path(os.environ.get("UPDATE_DIR", BASE_DIR / "updates"))
UPDATE_INCOMING_DIR = Path(os.environ.get("UPDATE_INCOMING_DIR", UPDATE_DIR / "incoming"))
UPDATE_STATUS_DIR = Path(os.environ.get("UPDATE_STATUS_DIR", UPDATE_DIR / "status"))

# Progress of a running legacy-DB import. Deliberately in the container's temp dir and not
# in the database: the import itself runs in one big transaction, so progress written to the
# database would only become visible once it is over. A file is also shared between the
# gunicorn workers, so the browser's poll sees the job no matter which worker answers it.
IMPORT_STATUS_FILE = Path(os.environ.get("IMPORT_STATUS_FILE", Path(tempfile.gettempdir()) / "coffeepy-import.json"))

# The USB backup stick (coffee/backups.py): the host's storage helper mounts it under
# USB_DIR and reports through files in STORAGE_DIR - both mounted into web and backup
# (docker-compose.yml). A restore runs in the background like the import, so the same
# reasoning puts its progress in a file.
USB_DIR = Path(os.environ.get("USB_DIR", BASE_DIR / "data" / "usb"))
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "data" / "storage"))
# The device's .env, read-only - a backup carries it (docker-compose.yml mounts it).
COFFEEPY_ENV_FILE = Path(os.environ.get("COFFEEPY_ENV_FILE", BASE_DIR / ".env"))
RESTORE_STATUS_FILE = Path(os.environ.get("RESTORE_STATUS_FILE", Path(tempfile.gettempdir()) / "coffeepy-restore.json"))
# Written while a restore runs, so the kiosk shows "back in a moment" (install/maintenance.sh
# writes the same file during installs and updates; Caddy serves it).
MAINTENANCE_DIR = Path(os.environ.get("MAINTENANCE_DIR", BASE_DIR / "data" / "maintenance"))

# Which version this is: coffeepy-version.json, written into the tree by scripts/write_version.sh
# before it is built or shipped (git describe, and the commit) - "dev" without one. Backups and
# update bundles carry it, the dashboard lists it (dashboard/versions.py).
def _build_info():
    try:
        info = json.loads((BASE_DIR / "coffeepy-version.json").read_text())
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


COFFEEPY_BUILD = _build_info()
COFFEEPY_VERSION = os.environ.get("COFFEEPY_VERSION") or COFFEEPY_BUILD.get("version") or "dev"

# Standalone WiFi AP credentials (see install/hostapd.conf.template) - only used to render
# the kiosk setup-wizard's WiFi QR code; blank on deployments that join an existing network.
AP_SSID = os.environ.get("AP_SSID", "")
AP_PASSPHRASE = os.environ.get("AP_PASSPHRASE", "")

# Django only prints request errors to the console with DEBUG on, and mails them otherwise -
# on an offline box with no ADMINS that meant a 500 left no trace at all, not even in
# `docker compose logs web`.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        # A host missing from ALLOWED_HOSTS is a bare 400 in the browser - this says which.
        "django.security": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
