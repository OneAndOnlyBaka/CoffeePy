import subprocess
from datetime import datetime

from django.utils import timezone


def set_system_datetime(new_datetime: datetime) -> None:
    """Set the host's wall clock. Requires CAP_SYS_TIME (granted to the `web` and `backup`
    compose services) - this is the offline-appliance fallback for when the RTC drifts or
    isn't fitted, ported from the legacy lib/clock/adjustment.py.

    A naive datetime is the device's local time (TIME_ZONE) - what the dashboard's form
    sends. It goes to `date` as a Unix time: the container runs in UTC, and a local
    wall-clock string handed to it set the clock off by the UTC offset."""
    if timezone.is_naive(new_datetime):
        new_datetime = timezone.make_aware(new_datetime)
    set_system_epoch(new_datetime.timestamp())


def set_system_epoch(epoch: float) -> None:
    subprocess.run(["date", "-s", f"@{int(epoch)}"], check=True, capture_output=True)
    # No /dev/rtc0 in a container, so this usually fails: the host's rtc_sync.sh writes the
    # RTC on its next run instead, once it sees the system clock a minute or more off it.
    subprocess.run(["hwclock", "-w"], check=False, capture_output=True)
