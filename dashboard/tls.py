"""Certificates for the Caddy reverse proxy, and the Caddyfile that uses them.

Everything reaches the app through Caddy (docker-compose.yml): port 443 for the network,
and 127.0.0.1:8443 for the kiosk's own browser - the only place /kiosk/ is served. This
module owns what Caddy needs for that, in settings.TLS_DIR (./data/tls on the host):

  ca.crt / ca.key          the device's own CA, created on first start, 10 years. Phones
                           and PCs install ca.crt once (offered for download without a
                           login, see views.certificate_download); the kiosk's Chromium
                           gets it in its NSS store from install/kiosk/openbox-autostart.
  server.crt / .key        signed by that CA for settings.TLS_HOSTNAMES - port 443.
  kiosk.crt / .key         signed by that CA for localhost - port 8443. Never replaced by
                           an uploaded certificate, so the kiosk can't be locked out by one.
  custom.crt / .key        an admin's own certificate (chain + key), if uploaded; it
                           replaces server.crt on port 443 while it is there.
  Caddyfile                written here, never by hand.

Why Caddy is driven by writing its config: it runs with --watch, but reloads only when the
*parsed* config differs - the same file paths with new certificates behind them would be
ignored. So the files Caddy loads carry their fingerprint in the name (live-<hash>.crt),
and a new certificate means a new path. That also keeps the web container away from
Docker and from Caddy's admin API.

Uses the openssl CLI (in the image, see Dockerfile): the cryptography package has no
armv7 wheel, and the CLI does everything needed here.
"""
import fcntl
import hashlib
import ipaddress
import os
import re
import secrets
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext as _

CA_DAYS = 3650
# Apple refuses server certificates from user-installed CAs that are valid for longer than
# 825 days - and an iPhone that refuses it shows the same warning as no CA at all.
LEAF_DAYS = 800
RENEW_BEFORE_DAYS = 60
KIOSK_HOSTNAMES = ["localhost", "127.0.0.1"]


class TLSError(Exception):
    """A problem worth showing to the admin as it is (bad upload, openssl failure)."""


def tls_dir() -> Path:
    return Path(settings.TLS_DIR)


def _path(name) -> Path:
    return tls_dir() / name


def _openssl(*args, input_bytes=None) -> str:
    result = subprocess.run(["openssl", *args], input=input_bytes, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise TLSError(stderr or _("openssl %(command)s failed") % {"command": args[0]})
    return result.stdout.decode(errors="replace")


@contextmanager
def _locked():
    """The migrate, web and backup containers all call ensure(); only one may write."""
    tls_dir().mkdir(parents=True, exist_ok=True)
    with open(_path(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _write(path: Path, data: bytes, mode: int):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _san_entry(name: str) -> str:
    try:
        ipaddress.ip_address(name)
        return f"IP:{name}"
    except ValueError:
        return f"DNS:{name}"


def cert_sans(cert_path: Path) -> set[str]:
    """The certificate's subjectAltNames as {"DNS:x", "IP:y"}."""
    out = _openssl("x509", "-in", str(cert_path), "-noout", "-ext", "subjectAltName")
    names = set()
    for entry in re.split(r",\s*", out.split("\n", 1)[1].strip()) if "\n" in out else []:
        kind, _, value = entry.partition(":")
        if not value:  # no subjectAltName extension at all
            continue
        names.add(f"{'IP' if kind.strip() == 'IP Address' else kind.strip()}:{value.strip()}")
    return names


def _valid_for(cert_path: Path, days: int) -> bool:
    """Still valid `days` from now (openssl -checkend exits non-zero otherwise)."""
    try:
        _openssl("x509", "-in", str(cert_path), "-noout", "-checkend", str(days * 86400))
        return True
    except TLSError:
        return False


def _create_ca():
    suffix = secrets.token_hex(3)  # tells two devices' CAs apart in a phone's trust list
    _openssl(
        "req", "-x509", "-new", "-nodes",
        "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
        "-keyout", str(_path("ca.key")), "-out", str(_path("ca.crt")),
        "-days", str(CA_DAYS), "-subj", f"/O=CoffeePy/CN=CoffeePy device CA {suffix}",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash",
    )
    os.chmod(_path("ca.key"), 0o600)
    os.chmod(_path("ca.crt"), 0o644)


def _issue(name: str, hostnames: list[str]):
    """<name>.crt/.key for `hostnames`, signed by the device CA."""
    with tempfile.TemporaryDirectory(dir=tls_dir()) as tmp:
        key, csr, ext = Path(tmp, "key"), Path(tmp, "csr"), Path(tmp, "ext")
        _openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key))
        _openssl("req", "-new", "-key", str(key), "-subj", f"/O=CoffeePy/CN={hostnames[0]}", "-out", str(csr))
        ext.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=serverAuth\n"
            "authorityKeyIdentifier=keyid\n"
            f"subjectAltName={','.join(_san_entry(h) for h in hostnames)}\n"
        )
        cert = _openssl(
            "x509", "-req", "-in", str(csr), "-CA", str(_path("ca.crt")), "-CAkey", str(_path("ca.key")),
            "-set_serial", "0x" + secrets.token_hex(16), "-days", str(LEAF_DAYS), "-extfile", str(ext),
        )
        _write(_path(f"{name}.key"), key.read_bytes(), 0o600)
        _write(_path(f"{name}.crt"), cert.encode(), 0o644)


def _needs_issue(name: str, hostnames: list[str]) -> bool:
    cert = _path(f"{name}.crt")
    if not cert.exists() or not _path(f"{name}.key").exists():
        return True
    if not _valid_for(cert, RENEW_BEFORE_DAYS):
        return True
    try:
        _openssl("verify", "-CAfile", str(_path("ca.crt")), str(cert))  # a new CA voids it
    except TLSError:
        return True
    return cert_sans(cert) != {_san_entry(h) for h in hostnames}


def has_custom() -> bool:
    return _path("custom.crt").exists() and _path("custom.key").exists()


def ensure() -> list[str]:
    """Creates or renews whatever is missing, stale or issued for other hostnames, and
    rewrites the Caddyfile if that changed anything. Safe to call from every container on
    every start (and periodically - the backup loop does). Returns what it did."""
    hostnames = list(settings.TLS_HOSTNAMES)
    done = []
    with _locked():
        if not _path("ca.crt").exists() or not _path("ca.key").exists():
            _create_ca()
            done.append("created the device CA")
        if _needs_issue("server", hostnames):
            _issue("server", hostnames)
            done.append(f"issued the network certificate for {', '.join(hostnames)}")
        if _needs_issue("kiosk", KIOSK_HOSTNAMES):
            _issue("kiosk", KIOSK_HOSTNAMES)
            done.append("issued the kiosk certificate")
        if _write_caddyfile():
            done.append("rewrote the Caddyfile")
        if _write_maintenance_page():
            done.append("rewrote the maintenance page")
    return done


def _live_copy(source: str, prefix: str) -> str:
    """Copies <source>.crt/.key to live-<prefix>-<hash>.crt/.key and returns that stem -
    see the module docstring for why the name has to change with the content."""
    cert, key = _path(f"{source}.crt").read_bytes(), _path(f"{source}.key").read_bytes()
    stem = f"live-{prefix}-{hashlib.sha256(cert + key).hexdigest()[:12]}"
    if not _path(f"{stem}.crt").exists():
        _write(_path(f"{stem}.key"), key, 0o600)
        _write(_path(f"{stem}.crt"), cert, 0o644)
    return stem


def _primary_hostname(cert_path: Path) -> str:
    """What Caddy should assume for a TLS client that names no host - a phone opening
    https://192.168.4.1 sends no SNI at all."""
    for name in sorted(cert_sans(cert_path)):
        if name.startswith("DNS:"):
            return name[4:]
    return "localhost"


def _write_caddyfile() -> bool:
    network = _live_copy("custom" if has_custom() else "server", "net")
    kiosk = _live_copy("kiosk", "kiosk")
    content = f"""# Written by dashboard/tls.py (manage.py ensure_tls, the dashboard's certificate page).
# Hand edits are overwritten.
{{
\tadmin off
\t# Certificates come from tls.py; no ACME, and no port 80 to redirect from.
\tauto_https off
\tdefault_sni {_primary_hostname(_path(f"{network}.crt"))}
}}

# While an install or update runs (install/maintenance.sh): its status, straight from disk
# so it answers with the app down, and a "back in a moment" page instead of Caddy's bare
# 502 for whatever asks the app meanwhile.
(maintenance) {{
\thandle /maintenance/status.json {{
\t\theader Cache-Control no-store
\t\troot * /
\t\t# No file means no maintenance - an empty object, not a 404 every two seconds.
\t\t@idle not file
\t\trespond @idle "{{}}" 200
\t\tfile_server
\t}}
\thandle_errors 502 503 504 {{
\t\theader Cache-Control no-store
\t\troot * /tls
\t\trewrite * /maintenance.html
\t\tfile_server
\t}}
}}

# The network: dashboard, portal, admin. The kiosk is answered only on :8443 below.
:443 {{
\ttls /tls/{network}.crt /tls/{network}.key
\timport maintenance
\t@kiosk path /kiosk /kiosk/*
\trespond @kiosk "The kiosk is only available on the device itself." 404
\t# The app's own / leads to the kiosk - from the network, that is the dashboard.
\tredir / /dashboard/
\treverse_proxy web:8000
}}

# The kiosk's own browser. docker-compose.yml publishes this port on 127.0.0.1 only.
:8443 {{
\ttls /tls/{kiosk}.crt /tls/{kiosk}.key
\timport maintenance
\treverse_proxy web:8000
}}
"""
    caddyfile = _path("Caddyfile")
    if caddyfile.exists() and caddyfile.read_text() == content:
        return False
    _write(caddyfile, content.encode(), 0o644)
    for old in tls_dir().glob("live-*"):  # Caddy keeps what it loaded in memory
        if old.stem not in (network, kiosk):
            old.unlink()
    return True


def _write_maintenance_page() -> bool:
    """maintenance.html, which Caddy shows while the app is down (the Caddyfile's
    handle_errors). Rendered here because Caddy can't render templates - and in both
    languages, the device's default first, because it can't read a language cookie either."""
    from django.template.loader import render_to_string

    others = [code for code, _ in settings.LANGUAGES if code != settings.LANGUAGE_CODE]
    content = render_to_string("maintenance.html", {"languages": [settings.LANGUAGE_CODE] + others})
    page = _path("maintenance.html")
    if page.exists() and page.read_text() == content:
        return False
    _write(page, content.encode(), 0o644)
    return True


def install_custom(cert_pem: bytes, key_pem: bytes) -> list[str]:
    """Validates and installs an uploaded certificate (leaf first, then any chain) and its
    key. Raises TLSError for anything that would break the site; returns warnings for what
    merely looks wrong (hostnames it doesn't cover)."""
    with tempfile.TemporaryDirectory(dir=tls_dir()) as tmp:
        cert, key = Path(tmp, "cert"), Path(tmp, "key")
        cert.write_bytes(cert_pem)
        key.write_bytes(key_pem)
        try:
            cert_pub = _openssl("x509", "-in", str(cert), "-noout", "-pubkey")
        except TLSError:
            raise TLSError(_("The certificate is not a PEM-encoded X.509 certificate.")) from None
        try:
            key_pub = _openssl("pkey", "-in", str(key), "-pubout")
        except TLSError:
            raise TLSError(_("The key is not a PEM-encoded, unencrypted private key.")) from None
        if cert_pub.strip() != key_pub.strip():
            raise TLSError(_("The private key does not belong to this certificate."))
        if not _valid_for(cert, 0):
            raise TLSError(_("The certificate has expired."))
        warnings = []
        if not _valid_for(cert, 30):
            warnings.append(_("The certificate expires within 30 days."))
        for host in settings.TLS_HOSTNAMES:
            flag = "-checkip" if _san_entry(host).startswith("IP:") else "-checkhost"
            # Not _openssl(): a mismatch is a non-zero exit, and here it is the answer.
            check = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", flag, host], capture_output=True)
            if b"does match" not in check.stdout:
                warnings.append(_("It is not valid for %(host)s - browsers using that address will warn.") % {"host": host})
        with _locked():
            _write(_path("custom.key"), key_pem, 0o600)
            _write(_path("custom.crt"), cert_pem, 0o644)
            _write_caddyfile()
    return warnings


def remove_custom():
    with _locked():
        for name in ("custom.crt", "custom.key"):
            _path(name).unlink(missing_ok=True)
        _write_caddyfile()


def active_certificate() -> dict | None:
    """What port 443 presents right now, for the dashboard's certificate page."""
    path = _path("custom.crt") if has_custom() else _path("server.crt")
    if not path.exists():
        return None
    out = _openssl("x509", "-in", str(path), "-noout", "-subject", "-issuer", "-enddate", "-fingerprint", "-sha256")
    fields = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    return {
        "source": "custom" if has_custom() else "self-signed",
        "subject": fields.get("subject", "").strip(),
        "issuer": fields.get("issuer", "").strip(),
        "not_after": fields.get("notAfter", "").strip(),
        "fingerprint": fields.get("sha256 Fingerprint", fields.get("SHA256 Fingerprint", "")).strip(),
        "names": sorted(n.split(":", 1)[1] for n in cert_sans(path)),
    }


def download() -> tuple[bytes, str] | None:
    """What a phone or PC should install to trust port 443: the device CA, or - with an
    uploaded certificate - the chain as uploaded."""
    if has_custom():
        return _path("custom.crt").read_bytes(), "coffeepy-certificate.crt"
    if _path("ca.crt").exists():
        return _path("ca.crt").read_bytes(), "coffeepy-ca.crt"
    return None
