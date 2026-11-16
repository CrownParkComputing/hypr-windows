"""CUPS and SANE: what is attached, and whether it is actually there.

Shared by the dock (hypr-windows) and the standalone panel. Nothing here
touches the UI, and nothing here is quick: a sleeping network printer takes
about three seconds to refuse a connection and seven if mDNS has to fail name
resolution first, and `scanimage -L` takes ten seconds to report an empty bus.
Call all of it from a worker thread.
"""

import glob
import json
import os
import re
import socket
import subprocess
import time

SCAN_DIR = os.path.expanduser("~/Scans")


def run(args, timeout=5):
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)
        return done.stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def read_queue():
    """Everything CUPS can tell us locally. Fast: no network involved."""
    printers = {}

    for line in run(["lpstat", "-p"]).splitlines():
        match = re.match(r"printer (\S+) is (\w+)", line)
        if match:
            name, state = match.group(1), match.group(2)
            printers[name] = {
                "name": name, "state": state, "jobs": [], "uri": "",
                "default": False, "accepting": True, "reason": "",
            }
            if "disabled" in line:
                printers[name]["state"] = "disabled"
            # CUPS puts the reason in brackets after the state line.
            tail = line.split(" - ", 1)
            if len(tail) == 2:
                printers[name]["reason"] = tail[1].strip()

    for line in run(["lpstat", "-v"]).splitlines():
        match = re.match(r"device for (\S+): (.+)", line)
        if match and match.group(1) in printers:
            printers[match.group(1)]["uri"] = match.group(2).strip()

    default = run(["lpstat", "-d"])
    match = re.search(r"destination: (\S+)", default)
    if match and match.group(1) in printers:
        printers[match.group(1)]["default"] = True

    for line in run(["lpstat", "-o"]).splitlines():
        parts = line.split()
        if len(parts) >= 3 and "-" in parts[0]:
            owner = parts[0].rsplit("-", 1)[0]
            if owner in printers:
                printers[owner]["jobs"].append(parts[0])

    for line in run(["lpstat", "-a"]).splitlines():
        parts = line.split()
        if parts and parts[0] in printers:
            printers[parts[0]]["accepting"] = "not accepting" not in line

    return list(printers.values())


def uri_host(uri):
    """The host:port an ipp/socket/http URI points at, or None for USB."""
    match = re.match(r"(?:ipp|ipps|http|https|socket|lpd)://([^/:]+)(?::(\d+))?",
                     uri or "")
    if not match:
        return None
    port = int(match.group(2) or (631 if uri.startswith("ipp") else 9100))
    return match.group(1), port


def probe(uri, timeout=3.0):
    """Does the device actually answer?

    'online', 'local' (USB), 'absent' (the name does not resolve, so the
    device is not on the network at all) or 'refused' (it is there but not
    listening). Worth separating: absent means go and switch it on, refused
    means it is awake and something else is wrong.
    """
    target = uri_host(uri)
    if target is None:
        return "local" if uri else "unknown"
    try:
        with socket.create_connection(target, timeout=timeout):
            return "online"
    except socket.gaierror:
        return "absent"
    except OSError:
        return "refused"


def ink_levels(uri, timeout=6):
    """Marker levels straight from the device, when it is reachable.

    CUPS only caches these for a queue that has printed recently, so ask the
    printer itself; it is also the only way to get the marker names.
    """
    if not uri or uri_host(uri) is None:
        return []
    text = run(["ipptool", "-T", "3", "-t", uri,
                "get-printer-attributes.test"], timeout=timeout)
    levels, names = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("marker-levels"):
            levels = [int(v) for v in re.findall(r"-?\d+", line.split("=", 1)[-1])]
        elif line.startswith("marker-names"):
            names = [v.strip() for v in line.split("=", 1)[-1].split(",")]
    return list(zip(names or ["ink"] * len(levels), levels))


def list_scanners(timeout=40):
    """SANE devices. Slow - eight to ten seconds even to say 'none'.

    Parsed from the sentence scanimage prints, not from `-f`: this version
    accepts the format string and then ignores it, so a formatted parse
    silently finds nothing and the UI claims there is no scanner.

        device `fujitsu:ScanSnap iX1600:1901633' is a FUJITSU ScanSnap iX1600 scanner
    """
    text = run(["scanimage", "-L"], timeout=timeout)
    found = []
    for line in text.splitlines():
        match = re.match(r"device `([^']+)' is a (.+?)\s*$", line.strip())
        if not match:
            continue
        description = match.group(2)
        if description.endswith(" scanner"):
            description = description[:-len(" scanner")]
        parts = description.split(None, 1)
        found.append({"device": match.group(1),
                      "vendor": parts[0] if parts else "",
                      "model": parts[1] if len(parts) > 1 else ""})
    return found


def scan(device, source="ADF Front", mode="Color", dpi=300, out_dir=SCAN_DIR,
         options=None):
    """Scan whatever is in the feeder. Returns (files written, message).

    The backend's own defaults are Lineart at 600dpi from the front side,
    which gives a 5096x6600 bitonal page nobody wants; colour at 300 is the
    useful default. --batch takes every sheet in the hopper rather than one.
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    pattern = os.path.join(out_dir, "scan-%s-%%03d.png" % stamp)
    try:
        done = subprocess.run(
            ["scanimage", "-d", device, "--source", source, "--mode", mode,
             "--resolution", str(dpi), *(options or []),
             "--format=png", "--batch=" + pattern],
            capture_output=True, text=True, timeout=900)
    except (subprocess.SubprocessError, OSError) as error:
        return [], str(error)
    files = sorted(glob.glob(os.path.join(out_dir, "scan-%s-*.png" % stamp)))
    if files:
        return files, "%d page%s" % (len(files), "" if len(files) == 1 else "s")
    # scanimage says why on stderr: out of documents, cover open, jam...
    detail = (done.stderr or "").strip().splitlines()
    return [], detail[-1] if detail else "nothing scanned"


def scanner_options(device, timeout=40):
    """What this scanner actually offers: sources, modes, resolutions.

    Parsed from `scanimage -A`, which prints each option as

        --source ADF Front|ADF Back|ADF Duplex [ADF Front]
        --resolution 50..600dpi (in steps of 1) [600]

    so a pick list comes from the pipe-separated form and a range has to be
    turned into sensible steps. Falls back to the usual values when the
    backend will not say.
    """
    text = run(["scanimage", "-A", "-d", device], timeout=timeout)
    options = {}
    for line in text.splitlines():
        line = line.strip()
        for key in ("source", "mode", "resolution"):
            if not line.startswith("--%s " % key):
                continue
            body = line[len(key) + 3:].split("[")[0].strip()
            if "|" in body:
                options[key] = [v.strip() for v in body.split("|")]
            elif ".." in body:
                low, _, rest = body.partition("..")
                high = re.match(r"(\d+)", rest)
                try:
                    low, high = int(low), int(high.group(1))
                except (AttributeError, ValueError):
                    continue
                options[key] = [str(v) for v in (75, 150, 200, 300, 400, 600)
                                if low <= v <= high]
    options.setdefault("source", ["ADF Front", "ADF Back", "ADF Duplex"])
    options.setdefault("mode", ["Color", "Gray", "Lineart"])
    options.setdefault("resolution", ["150", "300", "600"])
    return options


def scanner_status(device, timeout=40):
    """What the scanner's own sensors say, so the panel can tell you it is
    ready rather than making you look at the machine.

    `scanimage -A` prints each sensor with its current value in brackets:

        --page-loaded[=(yes|no)] [yes] [hardware]
        --cover-open[=(yes|no)] [no] [hardware]

    On a sheet-fed scanner the paper chute has to be open to load anything,
    so 'cover open' is only worth reporting when there is no page waiting.
    """
    text = run(["scanimage", "-A", "-d", device], timeout=timeout)
    sensors = {}
    for line in text.splitlines():
        match = re.match(r"\s*--([a-z0-9-]+)\[=\(yes\|no\)\]\s+\[(yes|no)\]",
                         line)
        if match:
            sensors[match.group(1)] = match.group(2) == "yes"
    if not sensors:
        return {"ready": None, "text": "", "sensors": {}}

    loaded = sensors.get("page-loaded", False)
    if sensors.get("power-save"):
        return {"ready": False, "text": "asleep", "sensors": sensors}
    if sensors.get("omr-df"):
        return {"ready": False, "text": "double feed - clear it",
                "sensors": sensors}
    if loaded:
        return {"ready": True, "text": "paper loaded - ready", "sensors": sensors}
    if sensors.get("cover-open"):
        return {"ready": False, "text": "chute open, no paper",
                "sensors": sensors}
    return {"ready": False, "text": "no paper in the feeder",
            "sensors": sensors}


def device_mac(uri):
    """The device's MAC, for a wake-up packet.

    HP names itself HP<MAC> over mDNS, which is the only clue left once it
    has stopped answering; otherwise look for a neighbour entry whose address
    matches the host in the URI.
    """
    target = uri_host(uri)
    if not target:
        return None
    host = target[0]
    match = re.match(r"(?i)HP([0-9A-F]{12})\.?(?:local\.?)?$", host)
    if match:
        raw = match.group(1)
        return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).lower()
    for line in run(["ip", "neigh"]).splitlines():
        parts = line.split()
        if parts and parts[0] == host and "lladdr" in parts:
            return parts[parts.index("lladdr") + 1].lower()
    return None


def wake(mac, port=9):
    """Send a Wake-on-LAN magic packet.

    Best effort by nature: it only does anything if the device kept its
    network interface powered while asleep and has wake-on-LAN enabled. A
    printer that is properly off - or a battery one that has shut down -
    cannot be woken by anything a host can send.
    """
    try:
        clean = re.sub(r"[^0-9a-fA-F]", "", mac or "")
        if len(clean) != 12:
            return False, "no MAC known for this device"
        payload = b"\xff" * 6 + bytes.fromhex(clean) * 16
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for address in ("255.255.255.255", "192.168.255.255"):
            try:
                sock.sendto(payload, (address, port))
            except OSError:
                continue
        sock.close()
        return True, "wake-up sent to %s" % clean
    except (OSError, ValueError) as error:
        return False, str(error)


# How CUPS and IPP name trouble, in words worth reading on a panel.
STATE_REASONS = {
    "media-empty": "out of paper",
    "media-needed": "needs paper",
    "media-jam": "paper jam",
    "cover-open": "cover open",
    "door-open": "door open",
    "input-tray-missing": "tray missing",
    "marker-supply-empty": "out of ink",
    "marker-supply-low": "ink low",
    "marker-waste-full": "waste tank full",
    "toner-empty": "out of toner",
    "toner-low": "toner low",
    "offline-report": "reports itself offline",
    "connecting-to-device": "connecting",
    "timed-out": "timed out",
    "paused": "queue paused",
    "shutdown": "shut down",
    "spool-area-full": "spool full",
    "other": "needs attention",
}


def printer_status(name, timeout=8):
    """What the queue knows about the printer, the way the scanner's sensors
    tell you about the scanner.

    Read from the local CUPS queue rather than the device, so it still says
    something useful when the printer is asleep: CUPS keeps the last state,
    the reasons it gave and the ink levels it reported. Levels below zero
    are IPP's way of saying unknown, not an empty cartridge.
    """
    text = run(["ipptool", "-tv", "ipp://localhost/printers/%s" % name,
                "get-printer-attributes.test"], timeout=timeout)
    attributes = {}
    for line in text.splitlines():
        match = re.match(r"\s+([a-z0-9-]+) \([^)]*\) = (.*)$", line)
        if match:
            attributes[match.group(1)] = match.group(2).strip()

    state = attributes.get("printer-state", "")
    reasons = [r.strip() for r in attributes.get("printer-state-reasons", "")
               .split(",") if r.strip() and r.strip() != "none"]
    accepting = attributes.get("printer-is-accepting-jobs") == "true"
    try:
        queued = int(attributes.get("queued-job-count", "0"))
    except ValueError:
        queued = 0

    names = [n.strip() for n in attributes.get("marker-names", "").split(",")
             if n.strip()]
    levels = []
    for value in re.findall(r"-?\d+", attributes.get("marker-levels", "")):
        levels.append(int(value))
    inks = [(label, level) for label, level in zip(names, levels) if level >= 0]

    words = []
    for reason in reasons:
        key = reason.replace("-warning", "").replace("-error", "")
        words.append(STATE_REASONS.get(key, key.replace("-", " ")))
    if state == "processing":
        words.insert(0, "printing")
    elif state == "stopped":
        words.insert(0, "stopped")
    if not accepting:
        words.append("not accepting jobs")
    if queued:
        words.append("%d job%s waiting" % (queued, "" if queued == 1 else "s"))

    ready = state == "idle" and accepting and not reasons
    return {
        "ready": ready,
        "text": ", ".join(words) if words else ("ready" if ready else state),
        "inks": inks,
        "queued": queued,
    }


# -------------------------------------------------------------------- disks --
# Everything below is about block devices: what is attached, what is mounted,
# and what this session is allowed to mount without being asked for a
# password. udisks answers all three and is cheap to ask - a full dump is
# ~25ms - so unlike the printers there is nothing here that needs a long
# timeout. It is still called from a worker thread for consistency.

# Mount points the dock will not offer to unmount: pulling any of these out
# from under a running session is not a thing a button should make easy.
PROTECTED = ("/", "/boot", "/efi", "/usr", "/var", "/etc", "/srv", "/root",
             "/home", "/nix", "/opt", "/proc", "/sys", "[SWAP]")

# Where a volume has to be mounted to count as "something you plugged in"
# rather than part of the installed system.
EXTERNAL_ROOTS = ("/run/media/", "/media/", "/mnt/")


def run_full(args, timeout=25):
    """Like run(), but keeps the exit code and whatever went to stderr.

    Mounting reports its failures on stderr and says nothing on stdout, so
    the plain run() would turn every refusal into silence.
    """
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)
        text = (done.stdout or "") + (done.stderr or "")
        return done.returncode, text.strip()
    except subprocess.TimeoutExpired:
        return 1, "%s timed out" % args[0]
    except (subprocess.SubprocessError, OSError) as error:
        return 1, str(error)


def human(size):
    """Bytes as a panel-width string: 3.6T, 931G, 4.0G, 200M."""
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "-"
    for unit, step in (("T", 1 << 40), ("G", 1 << 30), ("M", 1 << 20),
                       ("K", 1 << 10)):
        if size >= step:
            value = size / step
            return "%.1f%s" % (value, unit) if value < 10 else \
                   "%d%s" % (round(value), unit)
    return "%dB" % size


def udisks_hints():
    """Per-device hints from one `udisksctl dump`.

    The one that matters is HintSystem. udisks sets it for anything that
    looks like part of the installed system - a disk that is not removable,
    or a volume named in fstab - and polkit's default rules only let an
    active local session mount and unmount devices *without* it. So this
    flag is not decoration: it is the difference between a button that works
    and a button that returns NotAuthorized unless a polkit agent is running
    to ask for a password.
    """
    blocks, drives, current, device = {}, {}, None, None
    for line in run(["udisksctl", "dump"], timeout=10).splitlines():
        if line.startswith("/org/freedesktop/UDisks2/"):
            current = line.rstrip(":")
            device = None
            continue
        if current is None:
            continue
        name, _, value = line.strip().partition(":")
        value = value.strip()
        if "/drives/" in current:
            if name in ("ConnectionBus", "Ejectable", "CanPowerOff",
                        "Removable", "Optical", "MediaAvailable", "Model",
                        "Vendor"):
                entry = drives.setdefault(current, {})
                entry[name] = (value == "true" if value in ("true", "false")
                               else value)
            continue
        if "/block_devices/" not in current:
            continue
        if name == "Device":
            device = value
            blocks.setdefault(device, {})
        elif device and name in ("HintSystem", "HintIgnore", "HintAuto"):
            blocks[device][name] = value == "true"
        elif device and name == "Drive":
            blocks[device]["Drive"] = value.strip("'")
    return blocks, drives


def _under(mount, root):
    """Is this mount point that path, or inside it?

    Spelt out because "/" is a prefix of every path on the system: a plain
    startswith() would make every mount look like the root filesystem.
    """
    return mount == root or (root != "/" and
                             mount.startswith(root.rstrip("/") + "/"))


def _volume(node, hints, parent):
    """One mountable thing, as the panel thinks of it."""
    path = node.get("path") or ""
    hint = hints.get(path, {})
    mounts = [m for m in (node.get("mountpoints") or []) if m]
    # btrfs subvolumes give one volume half a dozen mount points; the
    # shortest is the one worth naming.
    mount = sorted(mounts, key=len)[0] if mounts else ""
    # "Basic data partition" is what Windows writes into every GPT entry
    # it makes: true of a third of the partitions on this machine, and so
    # no use as a name. Where it is mounted says far more.
    label = node.get("label") or ""
    partlabel = (node.get("partlabel") or "").strip()
    if not label and partlabel.lower() not in ("", "basic data partition"):
        label = partlabel
    if not label and mount:
        label = os.path.basename(mount.rstrip("/")) or mount
    return {
        "path": path,
        "name": node.get("name") or "",
        "label": label,
        "fstype": node.get("fstype") or "",
        "size": node.get("size") or 0,
        "mount": mount,
        "mounts": mounts,
        "extra_mounts": max(0, len(mounts) - 1),
        "used": node.get("fsused"),
        "avail": node.get("fsavail"),
        "fssize": node.get("fssize"),
        "readonly": bool(node.get("ro")),
        "system": bool(hint.get("HintSystem", not parent.get("removable"))),
        "protected": any(_under(m, p) for m in mounts for p in PROTECTED),
        "external": any(m.startswith(root) for m in mounts
                        for root in EXTERNAL_ROOTS),
    }


def list_drives(fixed=False, keep=()):
    """Block devices grouped by the thing you plug in.

    `fixed` widens it from "removable, plus anything mounted under /mnt or
    /run/media" to every disk in the machine. The narrow default is what
    keeps a 300px dock readable: this machine has ten mountable volumes and
    only four of them are ones you would ever touch.

    `keep` names paths to list whatever the filter says. A drive that has
    just been powered off is the case: udisks drops its Drive object while
    the device node is still there, so for the moment between the eject and
    the kernel removing it, it is no longer removable and no longer mounted
    - it would silently fall out of the list it was just in and reappear
    under "all", which reads as the eject having gone wrong.
    """
    columns = ("NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS,RM,"
               "HOTPLUG,TRAN,MODEL,VENDOR,SERIAL,FSSIZE,FSUSED,FSAVAIL,RO")
    try:
        tree = json.loads(run(["lsblk", "-J", "-b", "-o", columns],
                              timeout=10) or "{}")
    except ValueError:
        return []
    hints, drive_info = udisks_hints()

    groups = []
    for node in tree.get("blockdevices", []):
        name = node.get("name") or ""
        kind = node.get("type") or ""
        children = node.get("children") or []
        if name.startswith("zram") or kind not in ("disk", "loop"):
            continue
        if not node.get("size") and not children:
            continue                      # an nbd slot with nothing in it
        bus = node.get("tran") or ("loop" if kind == "loop" else "")
        hint = hints.get(node.get("path") or "", {})
        drive = drive_info.get(hint.get("Drive", ""), {})
        removable = bool(node.get("rm") or node.get("hotplug")
                         or bus == "usb" or drive.get("Removable"))
        group = {
            "path": node.get("path") or "",
            "name": name,
            "kind": kind,
            "bus": bus or (drive.get("ConnectionBus") or ""),
            "size": node.get("size") or 0,
            "model": (drive.get("Model") or node.get("model") or "").strip(),
            "vendor": (drive.get("Vendor") or node.get("vendor") or "").strip(),
            "removable": removable,
            "optical": bool(drive.get("Optical")),
            "can_power_off": bool(drive.get("CanPowerOff")),
            "ejectable": bool(drive.get("Ejectable")),
            "volumes": [],
        }
        # A disk formatted whole - most USB sticks - is its own volume.
        for child in (children or [node]):
            if (child.get("type") or "") not in ("part", "disk", "loop",
                                                 "crypt", "lvm"):
                continue
            mounted = [m for m in (child.get("mountpoints") or []) if m]
            if not child.get("fstype") and not mounted:
                continue              # empty or unformatted: nothing to mount
            if child.get("fstype") == "swap":
                continue
            if hints.get(child.get("path") or "", {}).get("HintIgnore"):
                continue
            group["volumes"].append(_volume(child, hints, group))
        if not group["volumes"] and group["path"] not in keep:
            continue
        if (kind == "loop" and not fixed
                and not any(v["mount"] for v in group["volumes"])):
            continue                  # a backing image nobody is looking at
        wanted = (removable or group["path"] in keep
                  or any(v["external"] for v in group["volumes"]))
        if fixed or wanted:
            groups.append(group)
    return groups


def mount(path):
    """udisks does the mounting, so it lands where a file manager expects."""
    code, text = run_full(["udisksctl", "mount", "--no-user-interaction",
                           "-b", path])
    if code == 0:
        where = text.split(" at ", 1)[-1].strip(" .") if " at " in text else ""
        return True, "mounted%s" % (" at %s" % where if where else "")
    return False, _why(text, path)


def unmount(path):
    code, text = run_full(["udisksctl", "unmount", "--no-user-interaction",
                           "-b", path])
    if code == 0:
        return True, "%s unmounted" % os.path.basename(path)
    return False, _why(text, path)


def power_off(path):
    """Spin a USB disk down so it is safe to pull out."""
    code, text = run_full(["udisksctl", "power-off", "--no-user-interaction",
                           "-b", path])
    if code == 0:
        return True, "safe to unplug"
    return False, _why(text, path)


def loop_delete(path):
    """Detach a loop device - the eject of a file-backed disk."""
    code, text = run_full(["udisksctl", "loop-delete",
                           "--no-user-interaction", "-b", path])
    if code == 0:
        return True, "%s detached" % os.path.basename(path)
    return False, _why(text, path)


def _why(text, path):
    """Turn a D-Bus error into something worth reading on a panel."""
    name = os.path.basename(path)
    if "NotAuthorized.CanObtain" in text or "DismissedAuthorization" in text:
        return ("%s needs authorisation and no polkit agent is running "
                "(start hyprpolkitagent)" % name)
    if "NotAuthorized" in text:
        return "not allowed to do that to %s" % name
    if "target is busy" in text or "TargetBusy" in text:
        return "%s is in use - close what is reading it" % name
    if "AlreadyMounted" in text:
        return "%s is already mounted" % name
    if "NotMounted" in text:
        return "%s is not mounted" % name
    if "No such interface" in text or "not a mountable" in text:
        return "%s has no filesystem to mount" % name
    tail = text.rsplit(":", 1)[-1].strip()
    return tail[:120] or "could not do that to %s" % name
