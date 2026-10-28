"""The kiosk's night: Chromium ended and the screen dark between NightPolicy.off_at and
on_at - or the weekend's own rule. The containers can't touch the kiosk session, so the schedule goes to a file the host
reads (data/storage/night.json; install/kiosk_night.py decides from it). Written when an
admin saves it and on every minutely `tick` - so a restore, which brings back the database
but not the file, catches up within a minute."""
import json
import os
from datetime import time

from django.conf import settings

from .models import NightPolicy


def _rule(schedule, weekday):
    """The night that starts on this weekday (0 = Monday): "all_day", or its (off, on) times."""
    if weekday >= 5 and schedule.get("weekend") == "all_day":
        return "all_day"
    if weekday >= 5 and schedule.get("weekend") == "times":
        return time.fromisoformat(schedule["weekend_off"]), time.fromisoformat(schedule["weekend_on"])
    return time.fromisoformat(schedule["off"]), time.fromisoformat(schedule["on"])


def is_night(now, schedule):
    """Whether the datetime `now` falls into a night of `schedule` (night.json's content). A
    night usually runs past midnight (20:00-05:30), so it belongs to the day it started on:
    Friday's runs into Saturday, and the morning after an all-day Sunday ends at Monday's own
    on time. off before on (01:00-04:00) is a night within one day; equal times are no night."""
    if schedule.get("enabled") is not True:
        return False
    today, yesterday = _rule(schedule, now.weekday()), _rule(schedule, (now.weekday() - 1) % 7)
    t = now.time()
    if today == "all_day":
        return True
    off, on = today
    if off < on and off <= t < on or off > on and t >= off:
        return True
    if yesterday == "all_day":
        return off != on and t < on
    y_off, y_on = yesterday
    return y_off > y_on and t < y_on


def schedule(policy: NightPolicy | None = None) -> dict:
    policy = policy or NightPolicy.current()
    return {"enabled": policy.enabled, "off": f"{policy.off_at:%H:%M}", "on": f"{policy.on_at:%H:%M}",
            "weekend": policy.weekend, "weekend_off": f"{policy.weekend_off_at:%H:%M}",
            "weekend_on": f"{policy.weekend_on_at:%H:%M}", "tz": settings.TIME_ZONE}


def describe(policy: NightPolicy) -> str:
    """For the audit log: "20:00-05:30, weekend all_day", or "off"."""
    if not policy.enabled:
        return "off"
    text = f"{policy.off_at:%H:%M}-{policy.on_at:%H:%M}"
    if policy.weekend == NightPolicy.WEEKEND_TIMES:
        return f"{text}, weekend {policy.weekend_off_at:%H:%M}-{policy.weekend_on_at:%H:%M}"
    return text if policy.weekend == NightPolicy.WEEKEND_SAME else f"{text}, weekend all_day"


def write_file(policy: NightPolicy | None = None) -> bool:
    """Writes night.json when it differs from what is there. True if it wrote."""
    path = settings.STORAGE_DIR / "night.json"
    content = json.dumps(schedule(policy))
    try:
        if path.read_text() == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".night.{os.getpid()}.tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
    return True
