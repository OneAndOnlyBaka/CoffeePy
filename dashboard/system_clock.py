import subprocess
from datetime import datetime


def set_system_datetime(new_datetime: datetime) -> None:
    """Set the host's wall clock. Requires CAP_SYS_TIME (granted to the `web` compose
    service) - this is the offline-appliance fallback for when the RTC drifts or isn't
    fitted, ported from the legacy lib/clock/adjustment.py."""
    date_str = new_datetime.strftime("%Y-%m-%d %H:%M:%S")
    subprocess.run(["date", "-s", date_str], check=True)
    subprocess.run(["hwclock", "-w"], check=False)
