#!/usr/bin/env python3
"""Runs on the HOST (not in a container) as coffeepy-storage.service: the USB backup stick.

The dashboard can't touch block devices - the web container has no host privileges, on
purpose (see update_watcher.py). So this service does the device work, and the two talk
through files in data/storage/, which both see:

  status.json        written here every few seconds: the USB disks plugged in, which of
                     them is this device's backup stick, free space, the last request
  requests/*.json    written by the dashboard: format a disk as this device's stick,
                     adopt another device's stick, eject, apply restored settings, switch
                     SSH on or off - processed here, then deleted
  device-id          this device's identity, created here once; the stick carries it
  stick.json         the one stick this device backs up to - set when a stick is set up
                     (formatted or adopted); setting up another one forgets the old one

A backup stick is an ext4 file system labelled COFFEEBAK with .coffeepy-backup.json at its
root naming the device it belongs to and its own stick id. Only the stick in stick.json is
mounted for backups (/srv/coffeepy-usb/backup, read-write) - pulled out and plugged in
again, it simply carries on. Any other CoffeePy stick (another device's, or an earlier one
of this device that a newer stick replaced) is mounted read-only at
/srv/coffeepy-usb/foreign, so its backups can be restored or the stick taken over. Nothing is ever formatted unless the dashboard asked for exactly that disk, by path
and serial number - and never the disk the system itself runs from.

The containers see /srv/coffeepy-usb as /usb with rslave propagation, so a stick mounted
here later shows up in them without a restart (docker-compose.yml).

Python's standard library only: it runs on the plain Raspberry Pi OS python3.
"""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("COFFEEPY_DIR", "/opt/coffeepy"))
STORAGE_DIR = PROJECT_DIR / "data" / "storage"
REQUESTS_DIR = STORAGE_DIR / "requests"
USB_ROOT = Path(os.environ.get("COFFEEPY_USB_ROOT", "/srv/coffeepy-usb"))
OWN = USB_ROOT / "backup"
FOREIGN = USB_ROOT / "foreign"
PROBE = USB_ROOT / ".probe"
LABEL = "COFFEEBAK"
META = ".coffeepy-backup.json"
META_FORMAT = 1
INTERVAL = 2.0

# Sticks ejected through the dashboard stay unmounted until they are pulled out.
ejected = set()

# Debian's daemon and its socket activation - an image may use either. The installer switches
# both off (install.sh, step_ssh); the dashboard's System page switches the daemon on again.
SSH_UNITS = ("ssh.service", "ssh.socket")
SSH_SECONDS = 30  # how often the SSH state is asked for: every cycle would be a systemctl every 2s
ssh = {}


def log(message):
    print(message, flush=True)


def run(*args, stdin=None):
    """A command that has to work; its stderr becomes the error the dashboard shows."""
    result = subprocess.run(args, input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:2])}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def event(action, target="", details="", outcome="success", actor="system"):
    """One line for the audit log (data/storage/events.jsonl - the app collects it, see
    dashboard/audit.py). A single short append: atomic, so the app never reads half a line."""
    line = json.dumps({"time": time.time(), "action": action, "actor": actor, "target": target,
                       "details": details, "outcome": outcome}) + "\n"
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STORAGE_DIR / "events.jsonl", "a") as f:
            f.write(line)
    except OSError as exc:
        log(f"event {action}: {exc}")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)  # the dashboard polls it - never a half-written file


def configured_stick():
    """The stick this device backs up to, or None while none was ever set up."""
    try:
        return json.loads((STORAGE_DIR / "stick.json").read_text())
    except (OSError, ValueError):
        return None


def is_own(meta, our_id):
    stick = configured_stick()
    return bool(meta and stick and meta.get("device_id") == our_id and meta.get("stick_id") == stick.get("stick_id"))


def device_id():
    path = STORAGE_DIR / "device-id"
    try:
        return path.read_text().strip()
    except OSError:
        new = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new + "\n")
        return new


# --- what is plugged in -----------------------------------------------------------------

def lsblk():
    out = run("lsblk", "-J", "-b", "-o",
              "NAME,PATH,TYPE,RM,TRAN,SIZE,VENDOR,MODEL,SERIAL,LABEL,UUID,FSTYPE,MOUNTPOINTS")
    return json.loads(out).get("blockdevices", [])


def mounts():
    """mount point -> source device, from /proc/mounts."""
    table = {}
    with open("/proc/mounts") as f:
        for line in f:
            source, target = line.split()[:2]
            table[target.replace("\\040", " ")] = source
    return table


def system_disks(devices, mount_table):
    """Names of the disks holding / or /boot - those are never offered, even on USB (a Pi
    can boot from a USB SSD)."""
    system_sources = {mount_table.get("/"), mount_table.get("/boot/firmware"), mount_table.get("/boot")}
    names = set()
    for disk in devices:
        for node in [disk] + disk.get("children", []):
            if node.get("path") in system_sources:
                names.add(disk["name"])
    return names


def usb_disks(devices, mount_table):
    """The removable USB disks, each with its file systems ("volumes": its partitions, or the
    disk itself for a stick formatted without a partition table)."""
    skip = system_disks(devices, mount_table)
    disks = []
    for disk in devices:
        if disk.get("type") != "disk" or disk.get("tran") != "usb" or disk["name"] in skip:
            continue
        volumes = [c for c in disk.get("children", []) if c.get("type") == "part"] or ([disk] if disk.get("fstype") else [])
        disks.append({
            "path": disk["path"],
            "vendor": (disk.get("vendor") or "").strip(),
            "model": (disk.get("model") or "").strip(),
            "serial": (disk.get("serial") or "").strip(),
            "size": int(disk.get("size") or 0),
            "volumes": [{
                "path": v["path"], "label": v.get("label") or "", "uuid": v.get("uuid") or "",
                "fstype": v.get("fstype") or "", "size": int(v.get("size") or 0),
            } for v in volumes],
        })
    return disks


def read_meta(root):
    try:
        return json.loads((Path(root) / META).read_text())
    except (OSError, ValueError):
        return None


def probe_meta(volume):
    """The metadata of a stick that isn't mounted yet: mounted read-only for a moment."""
    PROBE.mkdir(parents=True, exist_ok=True)
    try:
        run("mount", "-o", "ro,noexec,nosuid,nodev", volume["path"], str(PROBE))
    except RuntimeError as exc:
        log(f"probe {volume['path']}: {exc}")
        return None
    try:
        return read_meta(PROBE)
    finally:
        subprocess.run(["umount", str(PROBE)], capture_output=True)


def space(root):
    try:
        st = os.statvfs(root)
    except OSError:
        return None
    return {"total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize}


# --- mounting -----------------------------------------------------------------------------

def unmount_gone(mount_table, present):
    """A stick pulled out without ejecting leaves its mount behind - detach it lazily."""
    for target in (OWN, FOREIGN):
        source = mount_table.get(str(target))
        if source and source not in present:
            log(f"{source} is gone - detaching {target}")
            event("backup.stick_removed", target=source, outcome="failure" if target == OWN else "success",
                  details="this device's backup stick was pulled out without ejecting" if target == OWN else "")
            subprocess.run(["umount", "-l", str(target)], capture_output=True)


def mount_sticks(disks, mount_table, our_id):
    mounted_sources = set(mount_table.values())
    for disk in disks:
        for volume in disk["volumes"]:
            if volume["label"] != LABEL or volume["fstype"] != "ext4":
                continue
            if volume["path"] in mounted_sources or volume["uuid"] in ejected:
                continue
            meta = probe_meta(volume)
            if not meta:
                continue
            target, options = (OWN, "rw,noatime,nosuid,nodev,noexec") if is_own(meta, our_id) \
                else (FOREIGN, "ro,nosuid,nodev,noexec")
            if str(target) in mount_table:
                continue  # one stick per role; a second one waits
            target.mkdir(parents=True, exist_ok=True)
            try:
                run("mount", "-o", options, volume["path"], str(target))
                log(f"mounted {volume['path']} at {target}")
                event("backup.stick_mounted", target=f"{disk['vendor']} {disk['model']} ({disk['serial']})".strip(),
                      details="this device's backup stick" if target == OWN else
                      f"another CoffeePy stick, read-only (set up on {meta.get('device_name', '?')})")
                mount_table[str(target)] = volume["path"]
            except RuntimeError as exc:
                log(f"mount {volume['path']}: {exc}")


def reconcile(mount_table, our_id):
    """A stick mounted in the wrong role: the foreign one that a restored device identity
    made this device's own again, or the own one a newer stick replaced - unmounted, so the
    next mount_sticks() puts it where it now belongs."""
    for target, belongs_here in ((FOREIGN, False), (OWN, True)):
        if str(target) in mount_table and is_own(read_meta(target), our_id) != belongs_here:
            log(f"{mount_table[str(target)]} changed role - remounting")
            run("umount", str(target))
            del mount_table[str(target)]


SETTINGS_KEYS = ("DJANGO_LANGUAGE_CODE", "PUBLIC_HOST", "TLS_EXTRA_HOSTNAMES", "TIME_ZONE", "AUDIT_RETENTION_DAYS",
                 "GUNICORN_WORKERS", "DJANGO_SECURE_COOKIES")


def do_apply_settings(request):
    """Settings from a restored backup into .env (only SETTINGS_KEYS - the database password
    and the secret key stay this installation's), then the containers are recreated with them."""
    values = {k: str(v) for k, v in (request.get("values") or {}).items() if k in SETTINGS_KEYS and "\n" not in str(v)}
    if not values:
        raise RuntimeError("No settings to apply.")
    env = PROJECT_DIR / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    seen = set()
    for index, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in values:
            lines[index] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{key}={value}" for key, value in values.items() if key not in seen]
    tmp = env.with_name(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, env)
    run("docker", "compose", "--project-directory", str(PROJECT_DIR), "up", "-d")
    return "applied " + ", ".join(sorted(values))


def mounted_info(target, mount_table, disks):
    source = mount_table.get(str(target))
    if not source:
        return None
    disk = next((d for d in disks if any(v["path"] == source for v in d["volumes"])), None)
    return {
        "volume": source,
        "disk": disk["path"] if disk else "",
        "model": " ".join(filter(None, [disk["vendor"], disk["model"]])) if disk else "",
        "serial": disk["serial"] if disk else "",
        "space": space(target),
        "meta": read_meta(target),
    }


def ssh_state():
    result = subprocess.run(("systemctl", "is-active", *SSH_UNITS), capture_output=True, text=True)
    return {"active": "active" in result.stdout.split(), "checked": time.time()}


def do_ssh(request):
    """On stays on, across reboots too, until the dashboard switches it off. Stopping it leaves
    the sessions that are open alone (ssh.service has KillMode=process)."""
    if request.get("enabled"):
        run("systemctl", "enable", "--now", "ssh.service")
        return "on"
    run("systemctl", "disable", "--now", "ssh.service")
    subprocess.run(("systemctl", "disable", "--now", "ssh.socket"), capture_output=True)  # absent on some images
    return "off"


def handle_ssh(request):
    """Not one of the stick's requests: its outcome goes into status.json's "ssh", not into
    "request", which the backup page follows."""
    action = "ssh.enable" if request.get("enabled") else "ssh.disable"
    actor = request.get("requested_by", "system")
    try:
        do_ssh(request)
        error = ""
        event(action, actor=actor)
    except Exception as exc:  # noqa: BLE001 - reported to the dashboard
        log(f"request {action} failed: {exc}")
        error = str(exc)
        event(action, actor=actor, details=error, outcome="failure")
    ssh.clear()
    ssh.update(ssh_state(), error=error)


# --- requests from the dashboard ----------------------------------------------------------

def find_disk(disks, path, serial):
    disk = next((d for d in disks if d["path"] == path), None)
    if disk is None:
        raise RuntimeError("The stick is no longer there.")
    if disk["serial"] != serial:
        raise RuntimeError("A different stick is plugged in now - nothing was changed.")
    return disk


def write_meta(root, request, our_id, disk, previous=None):
    """Marks the stick at `root` as this device's - and as the one: stick.json now names it,
    so whichever stick held that place before is forgotten."""
    meta = {
        "format": META_FORMAT,
        "device_id": our_id,
        "stick_id": str(uuid.uuid4()),
        "device_name": socket.gethostname(),
        "set_up_at": now_iso(),
        "set_up_by": request.get("requested_by", ""),
    }
    if previous:
        meta["previous_device_ids"] = sorted({*previous.get("previous_device_ids", []), previous.get("device_id", "")} - {"", our_id})
    write_json(Path(root) / META, meta)
    (Path(root) / "backups").mkdir(exist_ok=True)
    write_json(STORAGE_DIR / "stick.json", {
        "stick_id": meta["stick_id"],
        "set_up_at": meta["set_up_at"],
        "set_up_by": meta["set_up_by"],
        "model": " ".join(filter(None, [disk.get("vendor", ""), disk.get("model", "")])) if disk else "",
        "serial": disk.get("serial", "") if disk else "",
    })


def do_format(request, disks, mount_table, our_id):
    disk = find_disk(disks, request["path"], request.get("serial", ""))
    for volume in disk["volumes"]:
        for target, source in list(mount_table.items()):
            if source == volume["path"]:
                run("umount", target)
    run("wipefs", "--all", "--force", disk["path"])
    # One partition over the whole stick, MBR: readable by anything that reads ext4.
    run("sfdisk", "--wipe", "always", "--wipe-partitions", "always", disk["path"], stdin="label: dos\n,,83\n")
    subprocess.run(["udevadm", "settle"], capture_output=True)
    partition = next((c["path"] for d in lsblk() if d["path"] == disk["path"] for c in d.get("children", [])), None)
    if not partition:
        raise RuntimeError("The new partition did not show up.")
    run("mkfs.ext4", "-F", "-q", "-L", LABEL, "-m", "0", partition)
    OWN.mkdir(parents=True, exist_ok=True)
    if str(OWN) in mount_table:  # the stick this device used so far - forgotten from now on
        subprocess.run(["umount", str(OWN)], capture_output=True)
    run("mount", "-o", "rw,noatime,nosuid,nodev,noexec", partition, str(OWN))
    write_meta(OWN, request, our_id, disk)
    run("sync")
    return "formatted"


def do_adopt(request, disks, mount_table, our_id):
    """Another CoffeePy stick becomes this device's backup stick - its backups stay where
    they are, and the stick used so far is forgotten."""
    source = mount_table.get(str(FOREIGN))
    if not source:
        raise RuntimeError("There is no other CoffeePy stick plugged in.")
    previous = read_meta(FOREIGN)
    run("umount", str(FOREIGN))
    if str(OWN) in mount_table:
        subprocess.run(["umount", str(OWN)], capture_output=True)
    OWN.mkdir(parents=True, exist_ok=True)
    run("mount", "-o", "rw,noatime,nosuid,nodev,noexec", source, str(OWN))
    disk = next((d for d in disks if any(v["path"] == source for v in d["volumes"])), None)
    write_meta(OWN, request, our_id, disk, previous=previous)
    run("sync")
    return "adopted"


def do_eject(request, disks, mount_table):
    ejected_any = False
    for target in (OWN, FOREIGN):
        source = mount_table.get(str(target))
        if source:
            run("sync")
            run("umount", str(target))
            volume = next((v for d in disks for v in d["volumes"] if v["path"] == source), None)
            if volume:
                ejected.add(volume["uuid"])
            ejected_any = True
    if not ejected_any:
        raise RuntimeError("No stick is mounted.")
    return "ejected"


def handle_requests(disks, mount_table, our_id, last):
    for path in sorted(REQUESTS_DIR.glob("*.json")):
        try:
            request = json.loads(path.read_text())
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        action = request.get("action")
        if action == "ssh":
            handle_ssh(request)
            continue
        result = {"id": request.get("id", path.stem), "action": action, "state": "running", "started": now_iso()}
        last.clear()
        last.update(result)
        write_status(disks, mounts(), our_id, last)
        log(f"request {action}: {request}")
        try:
            if action == "format":
                outcome = do_format(request, disks, mount_table, our_id)
            elif action == "adopt":
                outcome = do_adopt(request, disks, mount_table, our_id)
            elif action == "eject":
                outcome = do_eject(request, disks, mount_table)
            elif action == "apply_settings":
                outcome = do_apply_settings(request)
            else:
                raise RuntimeError(f"Unknown request {action!r}.")
            last.update(state="done", message=outcome, finished=now_iso())
            event(f"backup.stick_{action}", actor=request.get("requested_by", "system"),
                  target=request.get("path", ""), details=outcome)
        except Exception as exc:  # noqa: BLE001 - reported to the dashboard
            log(f"request {action} failed: {exc}")
            last.update(state="error", message=str(exc), finished=now_iso())
            event(f"backup.stick_{action}", actor=request.get("requested_by", "system"),
                  target=request.get("path", ""), details=str(exc), outcome="failure")
        mount_table.clear()
        mount_table.update(mounts())


def write_status(disks, mount_table, our_id, last):
    own = mounted_info(OWN, mount_table, disks)
    foreign = mounted_info(FOREIGN, mount_table, disks)
    in_use = {(own or {}).get("disk"), (foreign or {}).get("disk")}
    write_json(STORAGE_DIR / "status.json", {
        "updated": time.time(),
        "device_id": our_id,
        "device_name": socket.gethostname(),
        # Set up but not plugged in (own is None) means: backups are not running.
        "configured": configured_stick(),
        "own": own,
        "foreign": foreign,
        # USB disks that are neither - what the dashboard offers to set up.
        "available": [d for d in disks if d["path"] not in in_use],
        "request": dict(last),
        "ssh": dict(ssh),
    })


def cycle(last):
    our_id = device_id()  # every time: a restore may have brought the device's old id back
    devices = lsblk()
    mount_table = mounts()
    disks = usb_disks(devices, mount_table)
    present = {v["path"] for d in disks for v in d["volumes"]}
    present_uuids = {v["uuid"] for d in disks for v in d["volumes"]}
    ejected.intersection_update(present_uuids)  # pulled out: next time it is mounted again
    unmount_gone(mount_table, present)
    mount_table = mounts()
    if time.time() - ssh.get("checked", 0) >= SSH_SECONDS:
        ssh.update(ssh_state())
    handle_requests(disks, mount_table, our_id, last)
    if last.get("state") == "done":
        devices = lsblk()
        disks = usb_disks(devices, mount_table)
    reconcile(mount_table, our_id)
    mount_sticks(disks, mount_table, our_id)
    write_status(disks, mounts(), our_id, last)


def main(once=False):
    for directory in (REQUESTS_DIR, OWN, FOREIGN):
        directory.mkdir(parents=True, exist_ok=True)
    log(f"storage helper up, device {device_id()}")
    last = {}
    while True:
        try:
            cycle(last)
        except Exception as exc:  # noqa: BLE001 - keep serving; the next cycle tries again
            log(f"cycle failed: {exc}")
        if once:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
