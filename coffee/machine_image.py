"""The picture that stands for the coffee machine on every screen (dashboard, kiosk, portal): the admin's own
photo if there is one (dashboard -> Coffee machine), else the drawing of its JURA product line
(static/machines/, drawn by scripts/make_machine_images.py) - found from the model name py-jura gives the
article number ("E6 (EB)", "GIGA X8c Professional"). The URL changes only with the photo, so the kiosk's
panel stays byte-identical from poll to poll."""
import hashlib
import re

from django.templatetags.static import static
from django.urls import reverse

# First match wins: "WE8" before "W8", "GIGA X8" before "GIGA 6", "ENA 8" before "E8".
FAMILIES = (("giga_x", r"GIGA [XW]\d"), ("giga", r"GIGA\b"), ("ena", r"ENA\b"), ("we", r"WE\d"), ("x", r"X\d"),
            ("w", r"W\d"), ("z", r"Z\d"), ("s", r"S\d"), ("j", r"J\d"), ("e", r"E\d"), ("d", r"D\d"), ("c", r"C\d"))
TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
# A phone's photo downsized by the upload page is ~100-200 KB; a Pi 3 decodes whatever it gets, every time.
MAX_BYTES = 1_500_000


def family(model: str) -> str:
    name = re.sub(r"^(JURA\s+)+", "", (model or "").strip(), flags=re.IGNORECASE).upper()
    return next((key for key, pattern in FAMILIES if re.match(pattern, name)), "generic")


def sniff(data: bytes) -> str | None:
    """The image's type from its first bytes - not from what the browser claims."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def url(settings_=None, model: str = "") -> str:
    from dashboard.models import CoffeeMachine  # noqa: PLC0415

    settings_ = settings_ or CoffeeMachine.current()
    if settings_.photo_digest and settings_.photo_type in TYPES:
        return reverse("machine_photo", args=[settings_.photo_digest, TYPES[settings_.photo_type]])
    return static(f"machines/{family(model or settings_.name)}.svg")
