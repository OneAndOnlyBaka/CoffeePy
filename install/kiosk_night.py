#!/usr/bin/env python3
"""Exits 0 while it is the kiosk's night, 1 otherwise - asked by the kiosk session
(install/kiosk/openbox-autostart) and the watchdog (install/kiosk_watchdog.sh). The schedule
is data/storage/night.json, written by the app (dashboard/night.py, whose is_night() this
mirrors - the host has no Django). Anything wrong with the file - missing, unreadable, half
written - counts as day: a broken schedule must never leave the kiosk dark.

    kiosk_night.py [--file PATH] [--now YYYY-MM-DDTHH:MM]
"""
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "data" / "storage" / "night.json"


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


def main(argv):
    path = Path(argv[argv.index("--file") + 1]) if "--file" in argv else DEFAULT_FILE
    try:
        schedule = json.loads(path.read_text())
        # The times are the dashboard's (TIME_ZONE), and the host's own zone may well be UTC.
        now = datetime.now(ZoneInfo(schedule["tz"])) if schedule.get("tz") else datetime.now()
        if "--now" in argv:
            now = datetime.fromisoformat(argv[argv.index("--now") + 1])
        night = is_night(now, schedule)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        night = False
    return 0 if night else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
