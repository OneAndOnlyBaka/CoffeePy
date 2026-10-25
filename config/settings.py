"""
Django settings for the CoffeePy project.
"""

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
if _public_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_public_host)


INSTALLED_APPS = [
    "django.contrib.admin",
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
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
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
UPDATE_INCOMING_DIR = Path(os.environ.get("UPDATE_INCOMING_DIR", BASE_DIR / "updates" / "incoming"))
UPDATE_STATUS_DIR = Path(os.environ.get("UPDATE_STATUS_DIR", BASE_DIR / "updates" / "status"))

# Progress of a running legacy-DB import. Deliberately in the container's temp dir and not
# in the database: the import itself runs in one big transaction, so progress written to the
# database would only become visible once it is over. A file is also shared between the
# gunicorn workers, so the browser's poll sees the job no matter which worker answers it.
IMPORT_STATUS_FILE = Path(os.environ.get("IMPORT_STATUS_FILE", Path(tempfile.gettempdir()) / "coffeepy-import.json"))

# Standalone WiFi AP credentials (see install/hostapd.conf.template) - only used to render
# the kiosk setup-wizard's WiFi QR code; blank on deployments that join an existing network.
AP_SSID = os.environ.get("AP_SSID", "")
AP_PASSPHRASE = os.environ.get("AP_PASSPHRASE", "")
