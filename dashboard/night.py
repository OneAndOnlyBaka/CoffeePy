"""The kiosk's night: the kiosk browser ended and the screen dark between NightPolicy.off_at and
on_at - or the weekend's own rule. The containers can't touch the kiosk session, so the schedule goes to a file the host
reads (data/storage/night.json; install/kiosk_night.py decides from it). Written when an
admin saves it and on every minutely `tick` - so a restore, which brings back the database
but not the file, catches up within a minute."""
import json
import os
import time as clock
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
    on time. off before on (01:00-04:00) is a night within one day; equal times are no night.
    Also night, whatever the time: the coffee machine gone (switched off) for machine_off_after seconds."""
    off_since = schedule.get("machine_off_since")
    if off_since is not None and now.timestamp() - float(off_since) >= float(schedule.get("machine_off_after", 300)):
        return True
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


# The coffee machine not found for this long counts as switched off (NightPolicy.follow_machine): long enough
# that a dropped Bluetooth link - a Pi 3's own radio loses one now and then - doesn't put the kiosk out.
MACHINE_OFF_SECONDS = 300
# The bridge says "not found" anew at every attempt (~every 100 s): older than this, it may be gone itself -
# a dead bridge must not keep the kiosk dark, so its word counts only while it is fresh.
BRIDGE_FRESH_SECONDS = 600


def machine_off_since(policy: NightPolicy) -> float | None:
    """Since when the bridge has looked for the machine and not found it (its state's missing_since) - only
    with follow_machine on, a machine set up, and a bridge that is there to say so."""
    from coffee.models import MachineStatus  # noqa: PLC0415 - coffee imports dashboard's models

    from .models import CoffeeMachine  # noqa: PLC0415

    if not policy.follow_machine or not CoffeeMachine.current().configured:
        return None
    state = MachineStatus.current().state or {}
    since, updated = state.get("missing_since"), float(state.get("updated") or 0)
    return float(since) if since and clock.time() - updated < BRIDGE_FRESH_SECONDS else None


def schedule(policy: NightPolicy | None = None) -> dict:
    policy = policy or NightPolicy.current()
    result = {"enabled": policy.enabled, "off": f"{policy.off_at:%H:%M}", "on": f"{policy.on_at:%H:%M}",
              "weekend": policy.weekend, "weekend_off": f"{policy.weekend_off_at:%H:%M}",
              "weekend_on": f"{policy.weekend_on_at:%H:%M}", "tz": settings.TIME_ZONE}
    off_since = machine_off_since(policy)
    if off_since is not None:
        result.update(machine_off_since=off_since, machine_off_after=MACHINE_OFF_SECONDS)
    return result


def describe(policy: NightPolicy) -> str:
    """For the audit log: "20:00-05:30, weekend all_day", or "off"."""
    machine = ", with the coffee machine" if policy.follow_machine else ""
    if not policy.enabled:
        return "with the coffee machine" if machine else "off"
    text = f"{policy.off_at:%H:%M}-{policy.on_at:%H:%M}{machine}"
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
