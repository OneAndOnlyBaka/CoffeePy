"""Best-effort discovery of this host's own reachable address, for QR codes and any
other link meant to be opened from a *different* device. request.build_absolute_uri()
alone isn't enough for that: it echoes back whatever Host header the browser used to
reach this page, which on the kiosk touchscreen is typically "localhost" (the kiosk
browser and the app run on the same device) - useless to a phone scanning a QR code.

detect_local_ip() is deliberately free of any Django import, since config/settings.py
calls it directly while settings are still being defined (to add the detected/configured
host to ALLOWED_HOSTS) - importing django.conf.settings at that point would try to read
attributes of the very module still being executed.
"""
import socket


def detect_local_ip() -> str:
    """Best-effort LAN IP for this host. Doesn't need actual connectivity: a UDP
    "connect" just asks the OS which route/source address it would use, without sending
    anything. Falls back to loopback if there's no route at all yet (e.g. no network
    interface configured).

    Caveat: this app always runs inside the `web` container (see docker-compose.yml), so
    this normally reports the container's own bridge-network address, not the Docker/
    Podman host's real LAN IP - a phone on the host's network can't reach a bridge IP.
    Only correct as a fallback for genuinely non-containerized setups; set PUBLIC_HOST
    explicitly otherwise (install/install.sh already does this for the Pi's AP,
    where it's a fixed, known-correct address rather than something to detect)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_host() -> str:
    """Hostname/IP to use for links meant to be opened from another device. PUBLIC_HOST
    (set on the Pi to its fixed AP address, 192.168.4.1 - see install/install.sh)
    always wins over autodetection: a host with more than one network interface up (e.g.
    ethernet *and* the WiFi AP) can't be disambiguated by routing alone, since the
    "default route" isn't necessarily the interface phones actually connect through."""
    from django.conf import settings

    return settings.PUBLIC_HOST or detect_local_ip()


def public_base_url(request) -> str:
    """scheme://public_host()[:port] - same shape as request.build_absolute_uri()'s
    origin, but with the host swapped for public_host()."""
    scheme = "https" if request.is_secure() else "http"
    existing_host = request.get_host()
    port = existing_host.rsplit(":", 1)[1] if ":" in existing_host else None
    if port and port not in ("80", "443"):
        return f"{scheme}://{public_host()}:{port}"
    return f"{scheme}://{public_host()}"
