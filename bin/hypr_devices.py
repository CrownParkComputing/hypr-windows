"""CUPS and SANE: what is attached, and whether it is actually there.

Shared by the dock (hypr-windows) and the standalone panel. Nothing here
touches the UI, and nothing here is quick: a sleeping network printer takes
about three seconds to refuse a connection and seven if mDNS has to fail name
resolution first, and `scanimage -L` takes ten seconds to report an empty bus.
Call all of it from a worker thread.
"""

import os
import re
import socket
import subprocess

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
    """Does the device actually answer? Returns 'online', 'offline' or 'local'."""
    target = uri_host(uri)
    if target is None:
        return "local" if uri else "unknown"
    try:
        with socket.create_connection(target, timeout=timeout):
            return "online"
    except OSError:
        return "offline"


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


def list_scanners(timeout=25):
    """SANE devices. Slow - ten seconds even when the answer is 'none'."""
    text = run(["scanimage", "-L", "-f", "%d|%v|%m%n"], timeout=timeout)
    found = []
    for line in text.splitlines():
        if line.count("|") == 2:
            device, vendor, model = line.split("|")
            found.append({"device": device.strip(), "vendor": vendor.strip(),
                          "model": model.strip()})
    return found
