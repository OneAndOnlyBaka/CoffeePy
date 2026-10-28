"""How the DS3231 real-time clock is doing - read from data/storage/rtc.json, which the host's
install/rtc_sync.sh writes on every run (coffeepy-rtc-sync.timer). The containers can't look
at /dev/rtc0 themselves. Shown on the dashboard's overview and, when it is broken, as a
warning sign on the kiosk - like the backup stick (coffee/backups.py health())."""
import json
import time

from django.conf import settings

# The timer runs every ten minutes; a status much older than that means it stopped running.
STALE_SECONDS = 3600
BROKEN = ("missing", "unreadable", "lost")


def health() -> dict:
    """{"state": ok | missing | unreadable | lost | unknown, "disturbed": bool, ...the file's fields}.
    unknown - no status yet, or an old one - is not shown on the kiosk: a development machine
    has no RTC check at all, and a device only gets its first one minutes after booting."""
    try:
        status = json.loads((settings.STORAGE_DIR / "rtc.json").read_text())
    except (OSError, ValueError):
        status = None
    checked = status.get("checked") if isinstance(status, dict) else None
    if not isinstance(checked, (int, float)) or time.time() - checked > STALE_SECONDS:
        return {"state": "unknown", "disturbed": False}
    state = status.get("state") if status.get("state") in BROKEN else "ok"
    return {**status, "state": state, "disturbed": state in BROKEN}
