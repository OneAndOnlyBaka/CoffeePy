import shutil

import psutil


def _cpu_temperature_celsius():
    """Raspberry Pi (and most Linux hosts with a thermal zone) expose this directly -
    None if unavailable (e.g. some containers, or non-Linux dev machines)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000
    except (OSError, ValueError):
        return None


def gather() -> dict:
    """Host-level CPU/RAM/disk snapshot for the dashboard's System page. All psutil/
    stdlib reads, no persistence - safe to call on every request (cpu_percent briefly
    blocks to sample, hence the short interval rather than 0)."""
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count() or 1,
        "cpu_temp": _cpu_temperature_celsius(),
        "memory_percent": memory.percent,
        "memory_used_mb": round(memory.used / (1024**2)),
        "memory_total_mb": round(memory.total / (1024**2)),
        "disk_percent": round(disk.used / disk.total * 100, 1) if disk.total else 0,
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
    }
