"""The ordered VM NIC contract; the first adapter is the management interface."""

import re
import uuid

from messages import _


def mac(value):
    return value.replace(":", "").replace("-", "").lower()


def adapters(spec, backend):
    fields = {"libvirt": ("network",), "hyperv": ("switchName", "switchId"),
              "proxmox": ("bridge",), "virtualbox": ("bridge",),
              "vmware": ("vmnet",)}[backend]
    legacy = {"name": "management", "mac": spec["mac"],
              **{key: spec[key] for key in fields if key in spec}}
    if backend == "vmware":
        legacy.setdefault("vmnet", "nat")
    if "networkAdapters" not in spec:
        return [legacy]
    values = spec["networkAdapters"]
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise ValueError(_("networkAdapters must contain 1–8 ordered adapters."))
    names, macs = set(), set()
    for adapter in values:
        if not isinstance(adapter, dict) or set(adapter) != {"name", "mac", *fields}:
            raise ValueError(_("Each network adapter requires name, mac and the backend attachment fields."))
        if not isinstance(adapter["name"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,14}", adapter["name"]) or adapter["name"] == "lo":
            raise ValueError(_("Adapter names must be unique Linux interface names of 1–15 lowercase characters."))
        value = adapter["mac"]
        pattern = r"[0-9A-Fa-f]{12}" if backend == "hyperv" else r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
        if not isinstance(value, str) or not re.fullmatch(pattern, value) or int(mac(value)[:2], 16) & 1 or mac(value) == "000000000000":
            raise ValueError(_("Each network adapter requires a nonzero unicast MAC address."))
        if backend == "libvirt" and not re.fullmatch(r"52:54:00(:[0-9a-f]{2}){3}", value):
            raise ValueError(_("Disallowed libvirt VM MAC address."))
        if backend == "vmware" and (not value.lower().startswith("00:50:56:") or int(value.split(":")[3], 16) > 0x3f):
            raise ValueError(_("A static VMware MAC must be in the range 00:50:56:00:00:00–00:50:56:3f:ff:ff."))
        if adapter["name"] in names or mac(value) in macs:
            raise ValueError(_("Network adapter names and MAC addresses must be unique within a VM."))
        names.add(adapter["name"])
        macs.add(mac(value))
        for key in fields:
            attachment = adapter[key]
            if not isinstance(attachment, str) or not attachment or any(ord(c) < 32 or ord(c) == 127 for c in attachment):
                raise ValueError(_("Invalid network adapter attachment."))
            if backend in ("libvirt", "proxmox") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", attachment):
                raise ValueError(_("Invalid network adapter attachment."))
            if key == "switchId" and str(uuid.UUID(attachment)) != attachment:
                raise ValueError(_("Switch ID must be a canonical UUID."))
            if key == "vmnet" and attachment != "nat" and not re.fullmatch(r"vmnet(?:[0-9]|1[0-9])", attachment):
                raise ValueError(_("VMware adapters require nat or a named vmnet0–vmnet19 attachment."))
    if mac(values[0]["mac"]) != mac(spec["mac"]) or any(values[0][key] != legacy.get(key) for key in fields):
        raise ValueError(_("The first adapter must match the primary MAC and host attachment; it provides management access."))
    return values


def libvirt_matches(domain, spec):
    expected = {(mac(item["mac"]), item["network"]) for item in adapters(spec, "libvirt")}
    actual = []
    for item in domain.findall("./devices/interface"):
        address, source = item.find("mac"), item.find("source")
        if item.get("type") != "network" or address is None or source is None:
            return False
        actual.append((mac(address.get("address", "")), source.get("network")))
    return len(actual) == len(expected) and set(actual) == expected


def guest_checks(spec, backend):
    """Check declared names/MACs and the SSH server address in the same guest session."""
    if "networkAdapters" not in spec:
        return []
    import shlex
    values = adapters(spec, backend)
    result = []
    for item in values:
        address = ":".join(mac(item["mac"])[i:i + 2] for i in range(0, 12, 2))
        result.append('test "$(cat /sys/class/net/' + item["name"] + '/address)" = ' + shlex.quote(address))
    result.append('ip -o -4 address show dev ' + shlex.quote(values[0]["name"])
                  + ' | awk \'{split($4,a,"/"); print a[1]}\' | grep -F -x -q -- '
                  + '"$(printf \'%s\\n\' "$SSH_CONNECTION" | cut -d \' \' -f 3)"')
    return result
