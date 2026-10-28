"""Keeps the device's clock from going backwards unnoticed.

The legacy device ran a year with nobody looking after it, and its clock was ten hours behind
at the end: plausible enough that nothing complained, and every booking from then on carried
the wrong time. The RTC check (coffee/rtc.py) can't see that - the module answered, with the
wrong time it had been given. Two things can, without a network:

- the clock going backwards within one boot (CLOCK_BOOTTIME keeps counting, whatever the
  wall clock does), and
- the clock being behind the newest audit entry - every booking, login and payment writes
  one, so the time can't really be earlier than that.

Either way the clock is put forward - never back: a wrong clock ahead can't be told from a
right one - and the kiosk and the dashboard show it until an admin sets or confirms the time
(dashboard "System time"). With NTP the clock is NTP's business and this does nothing; the
host reports that in rtc.json (install/rtc_sync.sh). Run every minute by the backup service.
"""
import json
import os
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings

from coffee import rtc
from coffee.models import AuditLogEntry

JUMP_SECONDS = 120    # backwards within one boot - more than NTP would ever slew
BEHIND_SECONDS = 300  # behind the newest audit entry
AHEAD_OF_ENTRY = 60   # where a clock behind the newest entry is put
# Stored as the key, shown translated (dashboard/templates/dashboard/_clock_corrected.html).
REASONS = {"went_back": "the clock went back", "behind_log": "the clock was behind the newest log entry"}


def _path() -> Path:
    return settings.STORAGE_DIR / "clock.json"


def _read() -> dict:
    try:
        state = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write(state: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".clock.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def _boot() -> tuple[str, float]:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot_id = ""
    return boot_id, time.clock_gettime(time.CLOCK_BOOTTIME)


def _newest_entry() -> float | None:
    """The newest audit entry since an admin last set the clock - one set back on purpose
    leaves entries in the "future" behind it, which must not pull it forward again."""
    last_set = (AuditLogEntry.objects.filter(action="system_time.set").order_by("-pk")
                .values_list("pk", flat=True).first()) or 0
    newest = (AuditLogEntry.objects.filter(pk__gt=last_set).order_by("-created_at")
              .values_list("created_at", flat=True).first())
    return newest.timestamp() if newest else None


def _remember(state: dict, wall: float) -> None:
    boot_id, boottime = _boot()
    state["seen"] = {"boot_id": boot_id, "boottime": boottime, "wall": wall}


def check() -> dict | None:
    """Puts a clock that went back forward again. Returns the correction, if one was made."""
    from dashboard import audit, system_clock

    health = rtc.health()
    if health["state"] == "unknown":
        return None  # not on a device, or its host checks stopped: nothing to go by
    state = _read()
    wall = time.time()
    correction = None
    if health.get("ntp") != "yes":
        boot_id, boottime = _boot()
        seen = state.get("seen") or {}
        target, reason = None, ""
        if seen.get("boot_id") == boot_id and boot_id:
            expected = seen["wall"] + (boottime - seen["boottime"])
            if wall < expected - JUMP_SECONDS:
                target, reason = expected, "went_back"
        newest = _newest_entry()
        if newest and wall < newest - BEHIND_SECONDS and (target is None or newest + AHEAD_OF_ENTRY > target):
            target, reason = newest + AHEAD_OF_ENTRY, "behind_log"
        if target:
            system_clock.set_system_epoch(target)
            correction = {"from": wall, "to": target, "reason": reason}
            state["corrected"] = correction
            audit.log_actor("clock check", "system_time.corrected", details=f"{_iso(wall)} -> {_iso(target)}: {REASONS[reason]}",
                            outcome=AuditLogEntry.OUTCOME_FAILURE)
            wall = target
    _remember(state, wall)
    _write(state)
    return correction


def corrected() -> dict | None:
    """The last correction - {"from", "to": aware datetimes, "reason": a REASONS key} - until
    an admin set or confirmed the time."""
    stored = _read().get("corrected")
    if not isinstance(stored, dict):
        return None
    try:
        return {"from": datetime.fromtimestamp(stored["from"], tz=dt_timezone.utc),
                "to": datetime.fromtimestamp(stored["to"], tz=dt_timezone.utc), "reason": stored.get("reason", "")}
    except (KeyError, TypeError, ValueError):
        return None


def confirmed() -> None:
    """An admin set or confirmed the time: it is right now, and the next check starts from it."""
    state = _read()
    state.pop("corrected", None)
    _remember(state, time.time())
    _write(state)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=dt_timezone.utc).isoformat(timespec="seconds")
