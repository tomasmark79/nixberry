"""Host adapter for Proxmox, VirtualBox and VMware; Python 3 on Linux and Windows.

Runs locally or over verified SSH. Accepts only declarations and fixed operations;
reset requires explicit permission and renewed ownership verification. Partial results are never overwritten automatically.
"""

from messages import _, forward_stderr

import hashlib
import errno
import csv
import ipaddress
import json
import locale
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import time
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from vm_network import adapters as network_adapters, mac as compact_mac


def run(argv, *, timeout=120, breakaway=False):
    # Windows SSH terminates job processes when the session closes. Hypervisor
    # startup must escape that job so a running VM survives the RPC response.
    options = {"creationflags": 0x01000000} if breakaway and os.name == "nt" else {}
    result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False,
                            env={**os.environ, "LC_ALL": "C"}, **options)
    def decode(value):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode(locale.getpreferredencoding(False), errors="replace")
    stdout, stderr = decode(result.stdout), decode(result.stderr)
    if result.returncode:
        raise RuntimeError(" ".join(argv[:2]) + ": " + (stderr.strip() or stdout.strip()))
    forward_stderr(stderr)
    return stdout.strip()


def private_directory(directory):
    directory.mkdir(mode=0o700)
    if os.name == "nt":
        # An elevated SSH session may assign ownership to Administrators. The GUI
        # of the same account has that group disabled in its restricted token;
        # explicitly grant the account SID without granting other users access.
        rows = list(csv.reader(run(["whoami.exe", "/user", "/fo", "csv", "/nh"]).splitlines()))
        if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-\d+(?:-\d+)+", rows[0][1]):
            raise RuntimeError(_("Cannot verify the user SID for the private VM directory."))
        run(["icacls.exe", str(directory), "/inheritance:r", "/grant:r",
             "*" + rows[0][1] + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])


def validate(spec, backend):
    if backend not in ("proxmox", "virtualbox", "vmware"):
        raise ValueError(_("Unsupported backend."))
    network_adapters(spec, backend)
    if spec["hostPlatform"] not in ("linux", "windows") or (backend == "proxmox" and spec["hostPlatform"] != "linux"):
        raise ValueError(_("Unsupported host system."))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", spec["hostName"]):
        raise ValueError(_("Invalid host hostname."))
    if not re.fullmatch(r"(?!(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$)[a-z][a-z0-9-]{0,62}", spec["name"]):
        raise ValueError(_("Invalid VM name: use 1–63 lowercase letters, digits and hyphens, starting with a letter; reserved Windows device names are not allowed."))
    identity = str(uuid.UUID(spec["uuid"]))
    if identity != spec["uuid"] or spec["ownerId"] != "nixberry-" + identity:
        raise ValueError(_("Invalid UUID or owner marker."))
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", spec["mac"]):
        raise ValueError(_("Invalid MAC address."))
    if int(spec["mac"][:2], 16) & 1:
        raise ValueError(_("Multicast MAC addresses are not allowed."))
    if backend == "vmware" and (not spec["mac"].lower().startswith("00:50:56:") or int(spec["mac"].split(":")[3], 16) > 0x3f):
        raise ValueError(_("A static VMware MAC must be in the range 00:50:56:00:00:00–00:50:56:3f:ff:ff."))
    for key, minimum, maximum in (("memoryMiB", 1024, 1048576), ("vcpus", 1, 1024), ("diskGiB", 8, 1048576)):
        if type(spec[key]) is not int or not minimum <= spec[key] <= maximum:
            raise ValueError(_("Invalid capacity: {key}").format(key=key))
    if backend == "proxmox":
        if type(spec["vmid"]) is not int or not 100 <= spec["vmid"] <= 999999999:
            raise ValueError(_("Invalid VMID."))
        for key in ("storage", "bridge", "node"):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", spec[key]):
                raise ValueError(_("Invalid Proxmox resource: {key}").format(key=key))
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", spec.get("isoStorage", "local")):
            raise ValueError(_("Invalid Proxmox ISO storage."))
    elif backend == "virtualbox":
        if not isinstance(spec["bridge"], str) or not spec["bridge"] or any(c in spec["bridge"] for c in '\r\n\x00'):
            raise ValueError(_("VirtualBox requires an existing bridge adapter."))
    if backend != "proxmox" and (not isinstance(spec["toolPath"], str) or not spec["toolPath"]):
        raise ValueError(_("The hypervisor tool path is missing."))
    if not isinstance(spec["storageDirectory"], str) or any(c in spec["storageDirectory"] for c in '\r\n\x00'):
        raise ValueError(_("Invalid storage path."))
    if backend == "proxmox" and "," in spec["storageDirectory"]:
        raise ValueError(_("The Proxmox import path must not contain a comma."))


def host_check(spec, backend, checked_tools=None):
    validate(spec, backend)
    if socket.gethostname().lower() != spec["hostName"].lower():
        raise RuntimeError(_("The host hostname does not match the declaration."))
    expected = "Windows" if spec["hostPlatform"] == "windows" else "Linux"
    if platform.system() != expected:
        raise RuntimeError(_("The host operating system does not match the declaration."))
    root = Path(spec["storageDirectory"])
    if not root.is_absolute() or root == Path(root.anchor) or not root.is_dir():
        raise RuntimeError(_("The dedicated storageDirectory must be an existing absolute directory outside the drive root."))
    for path in (root, *root.parents):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise RuntimeError(_("The storage path must not contain a symbolic link."))
    if backend == "proxmox":
        if socket.gethostname().split(".")[0] != spec["node"]:
            raise RuntimeError(_("SSH must connect directly to the declared Proxmox node."))
    tool = (backend, spec.get("toolPath"), spec.get("node"))
    if checked_tools is None or tool not in checked_tools:
        if backend == "proxmox":
            run(["pveversion"])
        else:
            run([spec["toolPath"], "--version"] if backend == "virtualbox" else [spec["toolPath"], "list"])
        if checked_tools is not None:
            checked_tools.add(tool)
    return root / spec["name"]


def owned(directory, spec):
    if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()) or not directory.is_dir():
        raise RuntimeError(_("The VM directory is missing or is a link."))
    marker = directory / "nixberry.json"
    if marker.is_symlink():
        raise RuntimeError(_("The owner marker must not be a link."))
    data = json.loads(marker.read_text(encoding="utf-8"))
    if data["spec"] != spec:
        raise RuntimeError(_("The saved VM declaration differs from the current one; nothing will be overwritten."))
    return data


def pairs(text):
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip().strip('"')
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        result[key] = value
    return result


def safe_addresses(values):
    found = set()
    networks = [ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    for value in values:
        try:
            address = ipaddress.ip_interface(value).ip
            if address.version == 4 and str(address) != "192.168.79.1" and any(address in n for n in networks):
                found.add(str(address))
        except ValueError:
            pass
    return sorted(found)


def image_name(backend):
    return "system." + {"proxmox": "qcow2", "virtualbox": "vdi", "vmware": "vmdk"}[backend]


def media_name(backend, marker):
    return "installer.iso" if marker.get("method", "image") == "iso" else image_name(backend)


def bios_uuid(spec):
    # SMBIOS 2.6+ stores the first three UUID fields in little-endian order.
    values = [f"{byte:02x}" for byte in uuid.UUID(spec["uuid"]).bytes_le]
    # VMX requires a hyphen between bytes eight and nine; otherwise VMware
    # rejects the UUID and assigns a different one, breaking declared identity.
    return " ".join(values[:8]) + "-" + " ".join(values[8:])


def vbox_registry(spec):
    machines = []
    for line in run([spec["toolPath"], "list", "vms"]).splitlines():
        match = re.fullmatch(r'"(.*)" \{([a-fA-F0-9-]+)\}', line)
        if not match:
            raise RuntimeError(_("Unknown VBoxManage list vms output."))
        machines.append({"name": match[1], "uuid": match[2].lower()})
    return machines


def vbox_guest_properties(text):
    # VirtualBox 7.2 normally prints a table; older versions print Name/value rows.
    properties = {}
    for line in text.splitlines():
        legacy = re.match(r"^Name: (/VirtualBox/[^,]+), value: ([^,]*)", line)
        table = re.match(r"^(/VirtualBox/\S+)\s*=\s*'([^']*)'\s+@", line)
        match = legacy or table
        if match:
            properties[match[1]] = match[2]
    return properties


def proxmox_registry():
    return json.loads(run(["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"]))


def network_conflicts(spec, backend):
    """Recheck explicit NIC MACs before writes, including stopped inventory where available."""
    if "networkAdapters" not in spec:
        return
    wanted = {compact_mac(item["mac"]) for item in network_adapters(spec, backend)}
    observed = []
    if backend == "virtualbox":
        for vm in vbox_registry(spec):
            info = pairs(run([spec["toolPath"], "showvminfo", vm["uuid"], "--machinereadable"]))
            observed += [value for key, value in info.items() if re.fullmatch(r"macaddress\d+", key)]
    elif backend == "proxmox":
        for vm in proxmox_registry():
            if vm.get("type") != "qemu":
                continue
            info = json.loads(run(["pvesh", "get", f'/nodes/{vm["node"]}/qemu/{vm["vmid"]}/config', "--output-format", "json"]))
            observed += [part.split("=", 1)[1] for key, value in info.items() if re.fullmatch(r"net\d+", key)
                         for part in value.split(",") if re.fullmatch(r"[A-Za-z0-9]+=(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", part)]
    else:
        # Workstation has no global stopped-VM registry. Inspect its dedicated
        # storage and registered running paths; external stopped VMs remain an operator boundary.
        paths = set(Path(spec["storageDirectory"]).rglob("*.vmx"))
        paths.update(Path(path) for path in run([spec["toolPath"], "list"]).splitlines()[1:])
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(_("VMX must not be a link."))
            info = pairs(path.read_text(encoding="utf-8"))
            observed += [value for key, value in info.items() if re.fullmatch(r"ethernet\d+\.address", key)]
    if wanted & {compact_mac(value) for value in observed}:
        raise RuntimeError(_("A declared adapter MAC is already used by another VM."))


def absent(spec, backend, directory):
    if directory.exists() or directory.is_symlink():
        raise RuntimeError(_("The destination directory already exists; refusing to overwrite an existing or partial result."))
    network_conflicts(spec, backend)
    if backend == "virtualbox":
        if any(vm["uuid"] == spec["uuid"] or vm["name"] == spec["name"] for vm in vbox_registry(spec)):
            raise RuntimeError(_("The VirtualBox VM name or UUID is already in use."))
        adapters = re.findall(r"^Name:\s+(.+)$", run([spec["toolPath"], "list", "bridgedifs"]), re.M)
        if any(item["bridge"] not in [adapter.strip() for adapter in adapters]
               for item in network_adapters(spec, backend)):
            raise RuntimeError(_("The declared VirtualBox bridge adapter does not exist."))
    elif backend == "proxmox":
        if any(vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"] for vm in proxmox_registry()):
            raise RuntimeError(_("The Proxmox VMID or name is already in use in the cluster."))
        storage = json.loads(run(["pvesh", "get", f'/nodes/{spec["node"]}/storage/{spec["storage"]}/status', "--output-format", "json"]))
        if not storage.get("active"):
            raise RuntimeError(_("The Proxmox storage is inactive."))
        required = spec["diskGiB"] * 1024 ** 3 + 4 * 1024 ** 2
        if storage.get("avail", 0) < required:
            raise RuntimeError(_("The Proxmox storage lacks space for the declared disk capacity and EFI."))
        if any(not Path("/sys/class/net", item["bridge"], "bridge").is_dir()
               for item in network_adapters(spec, backend)):
            raise RuntimeError(_("The Proxmox bridge does not exist."))
    else:
        # Workstation cannot inventory all stopped VMs. Reserve the name and
        # directory within the declared root and also check running VMs.
        for vmx in run([spec["toolPath"], "list"]).splitlines()[1:]:
            path = Path(vmx)
            if path.is_file():
                values = pairs(path.read_text(encoding="utf-8"))
                if values.get("displayName") == spec["name"] or values.get("annotation") == spec["ownerId"]:
                    raise RuntimeError(_("The matching VMware VM is already running."))


def network_matches(info, spec, backend):
    """Verify every enabled NIC, its slot, static identity and attachment."""
    expected = network_adapters(spec, backend)
    if backend == "virtualbox":
        enabled = {int(key[3:]) for key, value in info.items()
                   if re.fullmatch(r"nic\d+", key) and value not in ("none", "null")}
        if enabled != set(range(1, len(expected) + 1)):
            return False
        return all(info.get(f"nic{i}") == "bridged"
                   and compact_mac(info.get(f"macaddress{i}", "")) == compact_mac(item["mac"])
                   and info.get(f"bridgeadapter{i}") == item["bridge"]
                   for i, item in enumerate(expected, 1))
    if backend == "proxmox":
        enabled = {key for key in info if re.fullmatch(r"net\d+", key)}
        if enabled != {f"net{i}" for i in range(len(expected))}:
            return False
        for i, item in enumerate(expected):
            fields = dict(part.split("=", 1) for part in info[f"net{i}"].split(",") if "=" in part)
            if compact_mac(fields.get("virtio", "")) != compact_mac(item["mac"]) or fields.get("bridge") != item["bridge"]:
                return False
        return True
    enabled = {int(match[1]) for key, value in info.items()
               if (match := re.fullmatch(r"ethernet(\d+)\.present", key)) and value.upper() == "TRUE"}
    if enabled != set(range(len(expected))):
        return False
    for i, item in enumerate(expected):
        prefix = f"ethernet{i}."
        if (compact_mac(info.get(prefix + "address", "")) != compact_mac(item["mac"])
                or info.get(prefix + "addressType") != "static"
                or info.get(prefix + "connectionType") != ("nat" if item["vmnet"] == "nat" else "custom")
                or (item["vmnet"] != "nat" and info.get(prefix + "vnet") != item["vmnet"])):
            return False
    return True


def probe(spec, backend, directory, inventory=None):
    # Share only a fresh inventory within the current request.
    def read_once(key, query):
        if inventory is None:
            return query()
        if key not in inventory:
            inventory[key] = query()
        return inventory[key]

    if backend == "virtualbox":
        machines = read_once((backend, spec["toolPath"]), lambda: vbox_registry(spec))
        matches = [v for v in machines if v["uuid"] == spec["uuid"] or v["name"] == spec["name"]]
        if not matches:
            return {"state": "absent", "addresses": []}
        if len(matches) != 1 or matches[0] != {"name": spec["name"], "uuid": spec["uuid"]}:
            raise RuntimeError(_("The VirtualBox VM identity does not match."))
        owned(directory, spec)
        info = pairs(run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"]))
        marker = run([spec["toolPath"], "getextradata", spec["uuid"], "nixberry/owner"])
        disk = str(directory / image_name(backend))
        if (marker != "Value: " + spec["ownerId"] or not network_matches(info, spec, backend)
                or info.get("firmware", "").lower() not in ("efi", "efi64")
                or os.path.normcase(os.path.normpath(info.get("SATA-0-0", ""))) != os.path.normcase(os.path.normpath(disk))
                ):
            raise RuntimeError(_("VirtualBox owner, disk, firmware or network does not match."))
        values = []
        state = info["VMState"]
        if state == "running":
            properties = vbox_guest_properties(run([spec["toolPath"], "guestproperty", "enumerate", spec["uuid"]]))
            for key, value in properties.items():
                if (re.fullmatch(r"/VirtualBox/GuestInfo/Net/\d+/MAC", key)
                        and value.replace(":", "").lower() == spec["mac"].replace(":", "").lower()):
                    address = properties.get(key.removesuffix("/MAC") + "/V4/IP")
                    if address:
                        values.append(address)
        return {"state": "shut off" if state == "poweroff" else state, "addresses": safe_addresses(values)}
    if backend == "proxmox":
        machines = read_once((backend, "cluster"), proxmox_registry)
        matches = [vm for vm in machines if vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"]]
        if matches and (len(matches) != 1 or matches[0]["vmid"] != spec["vmid"] or matches[0].get("node") != spec["node"] or matches[0].get("type") != "qemu"):
            raise RuntimeError(_("The Proxmox VM identity or node does not match."))
        # Cluster inventory may lag actual state after create/start.
        local = read_once((backend, spec["node"]), lambda: json.loads(run([
            "pvesh", "get", f'/nodes/{spec["node"]}/qemu', "--output-format", "json"])))
        matches = [vm for vm in local if vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"]]
        if not matches:
            return {"state": "absent", "addresses": []}
        if len(matches) != 1 or matches[0]["vmid"] != spec["vmid"]:
            raise RuntimeError(_("The Proxmox VM identity or node does not match."))
        owned(directory, spec)
        info = json.loads(run(["pvesh", "get", f'/nodes/{spec["node"]}/qemu/{spec["vmid"]}/config', "--output-format", "json"]))
        if (info.get("description") != spec["ownerId"] or info.get("name") != spec["name"]
                or "uuid=" + spec["uuid"] not in info.get("smbios1", "") or info.get("bios") != "ovmf"
                or not network_matches(info, spec, backend)):
            raise RuntimeError(_("The Proxmox configuration does not match the owner or hardware."))
        binding = directory / "hardware.json"
        if binding.is_symlink():
            raise RuntimeError(_("The disk record must not be a link."))
        disk = json.loads(binding.read_text(encoding="utf-8"))["diskVolume"]
        if info.get("virtio0", "").split(",")[0] != disk:
            raise RuntimeError(_("The Proxmox system disk was changed outside the declared creation."))
        current = json.loads(run(["pvesh", "get", f'/nodes/{spec["node"]}/qemu/{spec["vmid"]}/status/current', "--output-format", "json"]))
        state = current["status"]
        values = []
        if state == "running":
            try:
                network = json.loads(run(["qm", "guest", "cmd", str(spec["vmid"]), "network-get-interfaces"]))
                for adapter in network:
                    if adapter.get("hardware-address", "").lower() == spec["mac"].lower():
                        values.extend(item["ip-address"] for item in adapter.get("ip-addresses", []))
            except RuntimeError:
                pass
        return {"state": "shut off" if state == "stopped" else state, "addresses": safe_addresses(values)}
    vmx = directory / (spec["name"] + ".vmx")
    if not vmx.exists():
        return {"state": "absent", "addresses": []}
    owned(directory, spec)
    if vmx.is_symlink():
        raise RuntimeError(_("VMX must not be a link."))
    info = pairs(vmx.read_text(encoding="utf-8"))
    bios = bios_uuid(spec)
    if (info.get("annotation") != spec["ownerId"] or info.get("displayName") != spec["name"]
            or info.get("uuid.bios", "").lower().replace("-", " ").split() != bios.replace("-", " ").split()
            or not network_matches(info, spec, backend)
            or info.get("firmware") != "efi" or info.get("sata0:0.fileName") != image_name(backend)):
        raise RuntimeError(_("The VMware configuration does not match the declaration."))
    running = read_once((backend, spec["toolPath"], "running"), lambda: run([spec["toolPath"], "list"]).splitlines()[1:])
    active = any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(str(vmx)) for p in running)
    values = []
    if active:
        try:
            # Without -wait, headless Workstation may fail even with running Tools.
            # A short timeout keeps status fast while a VM starts.
            values = [run([spec["toolPath"], "getGuestIPAddress", str(vmx), "-wait"], timeout=5)]
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
    return {"state": "running" if active else "shut off", "addresses": safe_addresses(values)}


def vmx_text(spec):
    values = {
        ".encoding": "UTF-8", "config.version": "8", "virtualHW.version": "19",
        "displayName": spec["name"], "annotation": spec["ownerId"], "guestOS": "otherlinux-64",
        "firmware": "efi", "uefi.secureBoot.enabled": "FALSE",
        "memsize": str(spec["memoryMiB"]), "numvcpus": str(spec["vcpus"]),
        "uuid.bios": bios_uuid(spec),
        "uuid.action": "keep", "msg.autoAnswer": "TRUE",
        "sata0.present": "TRUE", "sata0:0.present": "TRUE", "sata0:0.fileName": "system.vmdk",
        "tools.syncTime": "FALSE",
        "pciBridge0.present": "TRUE", "pciBridge4.present": "TRUE", "pciBridge4.virtualDev": "pcieRootPort",
    }
    for index, adapter in enumerate(network_adapters(spec, "vmware")):
        prefix = f"ethernet{index}."
        values.update({prefix + "present": "TRUE", prefix + "virtualDev": "e1000e",
                       prefix + "connectionType": "nat" if adapter["vmnet"] == "nat" else "custom",
                       prefix + "addressType": "static", prefix + "address": adapter["mac"],
                       prefix + "startConnected": "TRUE"})
        if adapter["vmnet"] != "nat":
            values[prefix + "vnet"] = adapter["vmnet"]
    return "\n".join(key + ' = "' + value + '"' for key, value in values.items()) + "\n"


def proxmox_iso_medium(spec):
    storage = spec.get("isoStorage", "local")
    config = json.loads(run(["pvesh", "get", "/storage/" + storage, "--output-format", "json"]))
    if config.get("disable") or "iso" not in config.get("content", "").split(","):
        raise RuntimeError(_("Proxmox storage {storage} is not enabled for ISO content.").format(storage=storage))
    status = json.loads(run(["pvesh", "get", "/nodes/" + spec["node"] + "/storage/" + storage
                             + "/status", "--output-format", "json"]))
    if not status.get("active") or not status.get("enabled"):
        raise RuntimeError(_("ISO storage {storage} is inactive on the target node.").format(storage=storage))
    volume = storage + ":iso/nixberry-" + spec["uuid"] + ".iso"
    path = Path(run(["pvesm", "path", volume]))
    if not path.is_absolute() or not path.parent.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError(_("The managed ISO path is not a safe existing directory."))
    return volume, path


def create(spec, backend, directory):
    marker = owned(directory, spec)
    installing = marker.get("method", "image") == "iso"
    image = directory / media_name(backend, marker)
    if image.is_symlink() or not image.is_file():
        raise RuntimeError(_("The prebuilt image is missing."))
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != marker["sha256"]:
        raise RuntimeError(_("Image checksum does not match."))
    network_conflicts(spec, backend)
    if backend == "virtualbox":
        if any(vm["uuid"] == spec["uuid"] or vm["name"] == spec["name"] for vm in vbox_registry(spec)):
            raise RuntimeError(_("The VirtualBox VM already exists."))
        tool, identity = spec["toolPath"], spec["uuid"]
        run([tool, "createvm", "--name", spec["name"], "--uuid", identity, "--ostype", "Linux_64", "--basefolder", str(directory), "--register"])
        run([tool, "setextradata", identity, "nixberry/owner", spec["ownerId"]])
        run([tool, "modifyvm", identity, "--memory", str(spec["memoryMiB"]), "--cpus", str(spec["vcpus"]),
             # VirtualBox EFI reports SMBIOS 2.5; Linux does not reverse this UUID as for 2.6+.
             "--hardware-uuid", spec["uuid"], "--system-uuid-le", "off",
             "--firmware", "efi", "--ioapic", "on", "--boot1", "disk", "--boot2", "none", "--boot3", "none", "--boot4", "none",
             *[argument for i, adapter in enumerate(network_adapters(spec, backend), 1) for argument in
               (f"--nic{i}", "bridged", f"--bridge-adapter{i}", adapter["bridge"], f"--nic-type{i}", "82540EM",
                f"--mac-address{i}", adapter["mac"].replace(":", ""), f"--cable-connected{i}", "on")]])
        run([tool, "storagectl", identity, "--name", "SATA", "--add", "sata", "--controller", "IntelAhci", "--portcount", "2" if installing else "1", "--bootable", "on"])
        if installing:
            disk = directory / image_name(backend)
            run([tool, "createmedium", "disk", "--filename", str(disk), "--size", str(spec["diskGiB"] * 1024), "--format", "VDI"])
            run([tool, "storageattach", identity, "--storagectl", "SATA", "--port", "1", "--device", "0", "--type", "dvddrive", "--medium", str(image)])
            run([tool, "modifyvm", identity, "--boot1", "dvd", "--boot2", "disk"])
            image = disk
        # Copies of the same VDI may share a media UUID. Assign this new instance's
        # reserved identity before attaching its disk for the first time.
        run([tool, "storageattach", identity, "--storagectl", "SATA", "--port", "0", "--device", "0", "--type", "hdd",
             "--medium", str(image), "--setuuid", spec["uuid"]])
    elif backend == "proxmox":
        if any(vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"] for vm in proxmox_registry()):
            raise RuntimeError(_("The Proxmox VM already exists."))
        iso_volume = None
        if installing:
            iso_volume, iso_path = proxmox_iso_medium(spec)
            # This identity gets unique media; never take over an existing file.
            try:
                os.link(image, iso_path)
            except OSError as failure:
                if failure.errno != errno.EXDEV:
                    raise
                with image.open("rb") as source, iso_path.open("xb") as output:
                    shutil.copyfileobj(source, output)
            with iso_path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != marker["sha256"]:
                    raise RuntimeError(_("Managed ISO checksum does not match."))
        run(["qm", "create", str(spec["vmid"]), "--name", spec["name"], "--description", spec["ownerId"],
             "--smbios1", "uuid=" + spec["uuid"], "--memory", str(spec["memoryMiB"]), "--cores", str(spec["vcpus"]),
             "--bios", "ovmf", "--machine", "q35", "--ostype", "l26", "--agent", "enabled=1",
             *[argument for i, adapter in enumerate(network_adapters(spec, backend)) for argument in
               (f"--net{i}", "virtio=" + adapter["mac"] + ",bridge=" + adapter["bridge"])],
             "--efidisk0", spec["storage"] + ":0,efitype=4m,pre-enrolled-keys=0",
             "--virtio0", (spec["storage"] + ":" + str(spec["diskGiB"]) if installing else spec["storage"] + ":0,import-from=" + str(image)),
             *(["--ide2", iso_volume + ",media=cdrom"] if installing else []),
             "--boot", "order=ide2;virtio0" if installing else "order=virtio0", "--serial0", "socket", "--vga", "serial0"], timeout=1800)
        config = json.loads(run(["pvesh", "get", f'/nodes/{spec["node"]}/qemu/{spec["vmid"]}/config', "--output-format", "json"]))
        volume = config["virtio0"].split(",")[0]
        if not volume.startswith(spec["storage"] + ":"):
            raise RuntimeError(_("Proxmox created a disk outside the declared storage."))
        hardware = {"diskVolume": volume, "efiVolume": config["efidisk0"].split(",")[0],
                    **({"isoVolume": iso_volume} if installing else {})}
        with (directory / "hardware.json").open("x", encoding="utf-8") as stream:
            json.dump(hardware, stream)
    else:
        if installing:
            executable = "vmware-vdiskmanager.exe" if spec["hostPlatform"] == "windows" else "vmware-vdiskmanager"
            manager = str(Path(spec["toolPath"]).with_name(executable))
            run([manager, "-c", "-s", str(spec["diskGiB"]) + "GB", "-a", "lsilogic", "-t", "0", str(directory / image_name(backend))], timeout=1800)
        with (directory / (spec["name"] + ".vmx")).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(vmx_text(spec))
            if installing:
                stream.write('sata0:1.present = "TRUE"\nsata0:1.deviceType = "cdrom-image"\nsata0:1.fileName = "installer.iso"\nsata0:1.startConnected = "TRUE"\nbios.bootOrder = "cdrom,hdd"\n')
    return probe(spec, backend, directory)


def file_info(path):
    stat = path.stat()
    allocation = stat.st_blocks * 512 if hasattr(stat, "st_blocks") else None
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes
        high = wintypes.DWORD()
        function = ctypes.WinDLL("kernel32", use_last_error=True).GetCompressedFileSizeW
        function.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        function.restype = wintypes.DWORD
        ctypes.set_last_error(0)
        low = function(str(path), ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        allocation = (high.value << 32) + low
    return {"path": str(path), "fileBytes": stat.st_size, "logicalBytes": stat.st_size,
            "allocatedBytes": allocation, "modified": stat.st_mtime}


def virtualbox_inventory(spec, declared):
    machines, images, errors, references = [], [], [], {}
    tool = spec["toolPath"]
    for vm in vbox_registry(spec):
        row = {**vm, "classification": "unknown", "allocatedBytes": None,
               "declared": any(s["uuid"] == vm["uuid"] for s in declared)}
        machines.append(row)
        try:
            info = pairs(run([tool, "showvminfo", vm["uuid"], "--machinereadable"]))
            row["state"] = info["VMState"]
            # XML also includes snapshot settings, not just the currently attached disk.
            xml = ET.parse(info["CfgFile"]).getroot()
            for element in xml.iter():
                if element.tag.rsplit("}", 1)[-1] == "Image":
                    identity = element.get("uuid", "").strip("{}").lower()
                    references.setdefault(identity, []).append("VM/snapshot:" + vm["name"])
        except (OSError, RuntimeError, KeyError, ET.ParseError) as error:
            errors.append(vm["name"] + ": " + str(error))
    text = run([tool, "list", "hdds"])
    parents = {}
    for block in re.split(r"\n\s*\n", text):
        info = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                info[key.strip()] = value.strip()
        if not info:
            continue
        try:
            identity = str(uuid.UUID(info["UUID"]))
            parent = info.get("Parent UUID", "base")
            if parent not in ("base", "00000000-0000-0000-0000-000000000000"):
                parents[identity] = str(uuid.UUID(parent))
            path = Path(info["Location"])
            size = file_info(path)
            capacity = re.match(r"(\d+) MBytes", info.get("Capacity", ""))
            images.append({**size, "uuid": identity,
                           "capacityBytes": int(capacity[1]) * 1024 * 1024 if capacity else None})
            if info.get("In use by VMs"):
                references.setdefault(identity, []).append("VirtualBox:In use by VMs")
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            errors.append("VirtualBox medium: " + str(error))
    # A backing disk is still needed by unattached differencing images.
    for child, parent in parents.items():
        references.setdefault(parent, []).append("backing:" + child)
    by_id = {image["uuid"]: image for image in images}
    for row in machines:
        identifiers = {identity for identity, reasons in references.items() if "VM/snapshot:" + row["name"] in reasons}
        pending = list(identifiers)
        while pending:
            parent = parents.get(pending.pop())
            if parent and parent not in identifiers:
                identifiers.add(parent)
                pending.append(parent)
        if all(identity in by_id for identity in identifiers):
            row["allocatedBytes"] = sum(by_id[identity]["allocatedBytes"] for identity in identifiers)
        row["classification"] = "protected" if row.get("state") == "running" or row["declared"] else "unknown" if errors else "review"
    for image in images:
        image["protectedBy"] = references.get(image["uuid"], [])
        image["classification"] = "protected" if image["protectedBy"] else "unknown" if errors else "review"
    return {"machines": machines, "images": images, "errors": errors,
            "note": _("SSH user's VirtualBox registry, including media and snapshot references. review requires checking usage outside this registry. Do not add shared disk sizes across VMs.")}


def proxmox_inventory(spec, declared):
    machines, images, errors, references = [], [], [], {}
    prefix = f'/nodes/{spec["node"]}'
    for vm in proxmox_registry():
        if vm.get("type") != "qemu" or vm.get("node") != spec["node"]:
            continue
        row = {"name": vm.get("name"), "vmid": vm["vmid"], "state": vm["status"],
               "declared": any(s.get("vmid") == vm["vmid"] for s in declared),
               "capacityBytes": vm.get("maxdisk"), "allocatedBytes": None, "classification": "unknown"}
        machines.append(row)
        try:
            base = prefix + "/qemu/" + str(vm["vmid"])
            snapshots = json.loads(run(["pvesh", "get", base + "/snapshot", "--output-format", "json"]))
            paths = [(base + "/config", "VM:" + str(vm["vmid"]))]
            for snap in snapshots:
                if snap["name"] != "current":
                    paths.append((base + "/snapshot/" + snap["name"] + "/config", "snapshot:" + str(vm["vmid"]) + ":" + snap["name"]))
            for path, reason in paths:
                config = json.loads(run(["pvesh", "get", path, "--output-format", "json"]))
                for key, value in config.items():
                    if re.fullmatch(r"(?:virtio|scsi|sata|ide|efidisk|tpmstate|unused)\d+", key) and isinstance(value, str):
                        volume = value.split(",")[0]
                        references.setdefault(volume, []).append(reason)
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            errors.append(str(vm["vmid"]) + ": " + str(error))
        row["classification"] = "protected" if row["declared"] or row["state"] == "running" else "review"
    volumes = json.loads(run(["pvesh", "get", prefix + f'/storage/{spec["storage"]}/content',
                             "--content", "images", "--output-format", "json"]))
    for volume in volumes:
        row = {"path": volume["volid"], "capacityBytes": volume.get("size"), "allocatedBytes": volume.get("used"),
               "protectedBy": references.get(volume["volid"], []), "classification": "unknown"}
        try:
            path = Path(run(["pvesm", "path", volume["volid"]]))
            if path.is_file():
                row.update(file_info(path))
                row["volume"] = volume["volid"]
        except (OSError, RuntimeError) as error:
            errors.append(volume["volid"] + ": " + str(error))
        if row["protectedBy"]:
            row["classification"] = "protected"
        images.append(row)
    for machine in machines:
        def belongs(reason):
            return reason == "VM:" + str(machine["vmid"]) or reason.startswith("snapshot:" + str(machine["vmid"]) + ":")
        needed = {volume for volume, reasons in references.items() if any(belongs(reason) for reason in reasons)}
        attached = [image for image in images if any(belongs(reason) for reason in image["protectedBy"])]
        if attached and len(attached) == len(needed) and all(image["allocatedBytes"] is not None for image in attached):
            machine["allocatedBytes"] = sum(image["allocatedBytes"] for image in attached)
    return {"machines": machines, "images": images, "errors": errors,
            "note": _("Proxmox: node VMs and snapshots, images in the selected storage. Block/thin storage may not report physical allocation; null is not zero. Storage shared with other nodes prevents proof that orphaned volumes are unused.")}


def inventory(spec, backend, directory, declared):
    """Incomplete inventory never proves a resource can be removed."""
    machines, images, errors = [], [], []
    if backend in ("proxmox", "virtualbox"):
        result = proxmox_inventory(spec, declared) if backend == "proxmox" else virtualbox_inventory(spec, declared)
        # Include transferred images from interrupted creation before registration
        # in the hypervisor, as well as Proxmox staging after import.
        seen = {os.path.normcase(os.path.abspath(image["path"])) for image in result["images"]}
        def walk_error(error):
            result["errors"].append(str(error))
        for base, dirs, files in os.walk(directory.parent, followlinks=False, onerror=walk_error):
            dirs[:] = [name for name in dirs if not Path(base, name).is_symlink()
                       and not (hasattr(Path(base, name), "is_junction") and Path(base, name).is_junction())]
            for name in files:
                path = Path(base, name)
                if not name.endswith((".qcow2", ".vdi", ".iso")) or os.path.normcase(os.path.abspath(path)) in seen:
                    continue
                try:
                    result["images"].append({**file_info(path), "classification": "unknown",
                        "protectedBy": [], "note": _("Staging, recovery or interrupted creation; review is required.")})
                except OSError as error:
                    result["errors"].append(str(error))
        return result
    running = {os.path.normcase(os.path.abspath(path)) for path in run([spec["toolPath"], "list"]).splitlines()[1:]}
    configs, references = set(running), {}
    # Scope is explicit: the entire dedicated directory, including stopped VMware VMs.
    def walk_error(error):
        errors.append(str(error))
    for base, dirs, files in os.walk(directory.parent, followlinks=False, onerror=walk_error):
        dirs[:] = [d for d in dirs if not Path(base, d).is_symlink()
                   and not (hasattr(Path(base, d), "is_junction") and Path(base, d).is_junction())]
        for name in files:
            path = Path(base, name)
            if name.endswith((".qcow2", ".vdi", ".vmdk", ".vhdx", ".iso", ".vmx", ".vmsn", ".vmem", ".nvram")):
                try:
                    images.append({**file_info(path), "classification": "unknown", "protectedBy": [],
                                   "note": _("A disk, snapshot, backing image or recovery medium may be attached.")})
                except OSError as error:
                    errors.append(str(error))
            if backend == "vmware" and name.endswith(".vmx"):
                configs.add(os.path.normcase(os.path.abspath(path)))
    for config in sorted(configs):
        path = Path(config)
        try:
            info = pairs(path.read_text(encoding="utf-8"))
            known = any(s["ownerId"] == info.get("annotation") for s in declared)
            row = {"name": info.get("displayName"), "path": str(path),
                   "state": "running" if config in running else "shut off", "declared": known,
                   "classification": "protected" if known or config in running else "review", "allocatedBytes": 0}
            machines.append(row)
            # The owned directory also contains snapshots, NVRAM and saved state.
            # Age does not prove disuse; protect all its content.
            for image in images:
                if Path(image["path"]).parent == path.parent:
                    image["protectedBy"].append("VM/recovery:" + str(path))
                    image["classification"] = "protected"
                    row["allocatedBytes"] += image["allocatedBytes"]
            for key, value in info.items():
                if re.fullmatch(r"(?:sata|scsi|ide|nvme)\d+:\d+\.fileName", key):
                    attached = os.path.normcase(os.path.abspath(path.parent / value))
                    references.setdefault(attached, []).append("VM:" + str(path))
                    if Path(attached).parent != path.parent:
                        row["allocatedBytes"] = None
                        row["note"] = _("The VM has media outside its own directory; total allocation is unknown.")
        except (OSError, ValueError, KeyError) as error:
            errors.append(str(path) + ": " + str(error))
    for image in images:
        reasons = references.get(os.path.normcase(os.path.abspath(image["path"])), [])
        image["protectedBy"].extend(reasons)
        if reasons:
            image["classification"] = "protected"
    return {"machines": machines, "images": images, "errors": errors,
            "note": _("Inventory is read-only. VMware: running VMs and files in the dedicated directory, not all stopped VMs on the host. unknown is not a deletion candidate. Snapshots and saved states in each VM directory are also protected. On Windows, allocation is read through GetCompressedFileSizeW; logicalBytes is the file length.")}


def installer_media(spec, backend, directory, *, detach=False):
    marker = owned(directory, spec)
    if marker.get("method", "image") != "iso":
        raise RuntimeError(_("The VM was not created for ISO installation."))
    current = probe(spec, backend, directory)
    if current["state"] not in (("shut off",) if detach else ("running", "shut off")):
        raise RuntimeError(_("The VM is not in the required state for ISO operations."))
    path = str(directory / "installer.iso")
    if backend == "virtualbox":
        info = pairs(run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"]))
        if info.get("SATA-1-0") != path:
            raise RuntimeError(_("The VM is not using the expected ISO."))
        if detach:
            run([spec["toolPath"], "storageattach", spec["uuid"], "--storagectl", "SATA", "--port", "1", "--device", "0", "--type", "dvddrive", "--medium", "none"])
            run([spec["toolPath"], "modifyvm", spec["uuid"], "--boot1", "disk", "--boot2", "none"])
    elif backend == "proxmox":
        info = json.loads(run(["pvesh", "get", f'/nodes/{spec["node"]}/qemu/{spec["vmid"]}/config', "--output-format", "json"]))
        volume, iso_path = proxmox_iso_medium(spec)
        hardware = json.loads((directory / "hardware.json").read_text())
        if info.get("ide2", "").split(",")[0] != volume or hardware.get("isoVolume") != volume:
            raise RuntimeError(_("The VM is not using the expected ISO."))
        if not iso_path.is_file():
            raise RuntimeError(_("The managed installation ISO is missing."))
        with iso_path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != marker["sha256"]:
                raise RuntimeError(_("Managed ISO checksum does not match."))
        if detach:
            run(["qm", "set", str(spec["vmid"]), "--delete", "ide2", "--boot", "order=virtio0"])
            iso_path.unlink()
    else:
        vmx = directory / (spec["name"] + ".vmx")
        text = vmx.read_text(encoding="utf-8")
        info = pairs(text)
        if info.get("sata0:1.fileName") != "installer.iso" or info.get("sata0:1.present") != "TRUE":
            raise RuntimeError(_("The VM is not using the expected ISO."))
        if detach:
            text = text.replace('sata0:1.present = "TRUE"', 'sata0:1.present = "FALSE"').replace('bios.bootOrder = "cdrom,hdd"', 'bios.bootOrder = "hdd"')
            vmx.write_text(text, encoding="utf-8")
    return current


def reset_tree(directory, hardlink=None):
    # Includes Windows junctions and hard links to files outside the owned VM.
    pending = [directory]
    while pending:
        path = pending.pop()
        data = path.lstat()
        allowed_link = (hardlink is not None and path == directory / "installer.iso"
                        and data.st_nlink == 2 and not hardlink.is_symlink()
                        and os.path.samefile(path, hardlink))
        if (path.is_symlink() or path.is_mount()
                or getattr(data, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                or (stat.S_ISREG(data.st_mode) and data.st_nlink != 1 and not allowed_link)):
            raise RuntimeError(_("The VM directory contains a link, mount point or shared file; refusing reset."))
        if stat.S_ISDIR(data.st_mode):
            pending.extend(path.iterdir())


def reset_reference(value, base, directory):
    path = Path(value)
    path = path if path.is_absolute() else base / path
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def reset_checks(spec, backend, directory):
    snapshot = probe(spec, backend, directory)
    if snapshot["state"] not in ("running", "shut off"):
        raise RuntimeError(_("Reset requires an existing running or stopped VM; partial operation remnants will not be deleted."))
    marker = owned(directory, spec)
    hardlink = None
    if backend == "proxmox" and marker.get("method") == "iso":
        _volume, medium = proxmox_iso_medium(spec)
        if medium.exists():
            hardlink = medium
    reset_tree(directory, hardlink)
    result = {"state": snapshot["state"], "directory": str(directory), "uuid": spec["uuid"]}
    if backend != "proxmox":
        result["disk"] = str(directory / image_name(backend))
    if backend == "virtualbox":
        def attachments(info):
            controllers = [v for k, v in info.items() if re.fullmatch(r"storagecontrollername\d+", k)]
            return [v for k, v in info.items() if any(re.fullmatch(re.escape(c) + r"-\d+-\d+", k) for c in controllers)
                    and v not in ("none", "emptydrive", "")]
        details = pairs(run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"]))
        # createvm composes <basefolder>/<name>/<name>.vbox, including when
        # the supplied base folder already has the instance's name.
        if (not details.get("CfgFile") or Path(details["CfgFile"]).resolve()
                != (directory / spec["name"] / (spec["name"] + ".vbox")).resolve()):
            raise RuntimeError(_("The VirtualBox VM configuration is outside its dedicated directory; refusing reset."))
        if details.get("SnapshotCount", "0") != "0":
            raise RuntimeError(_("The VM has snapshots; refusing reset."))
        allowed = {str(directory / image_name(backend)), str(directory / "installer.iso")}
        if any(v not in allowed for v in attachments(details)):
            raise RuntimeError(_("The VM has additional or foreign media; refusing reset."))
        def medium_fields(text):
            result = {}
            for line in text.splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    result[key.strip()] = value.strip()
            return result
        medium = medium_fields(run([spec["toolPath"], "showmediuminfo", "disk", str(directory / image_name(backend))]))
        medium_id = str(uuid.UUID(medium["UUID"]))
        if medium.get("Parent UUID", "").lower() not in ("base", "00000000-0000-0000-0000-000000000000"):
            raise RuntimeError(_("The disk has a backing image or unverified origin; refusing reset."))
        usage = medium.get("In use by VMs", "")
        users = re.findall(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", usage)
        if any(identity.lower() != spec["uuid"] for identity in users):
            raise RuntimeError(_("The disk is used by another VM or snapshot; refusing reset."))
        for block in re.split(r"\n\s*\n", run([spec["toolPath"], "list", "hdds"])):
            if medium_fields(block).get("Parent UUID", "").lower() == medium_id:
                raise RuntimeError(_("A differencing image depends on this disk; refusing reset."))
        for peer in vbox_registry(spec):
            if peer["uuid"] == spec["uuid"]:
                continue
            info = pairs(run([spec["toolPath"], "showvminfo", peer["uuid"], "--machinereadable"]))
            references = attachments(info) + [info[k] for k in ("CfgFile", "SnapFldr", "NvramFile") if info.get(k)]
            if any(reset_reference(v, directory.parent, directory) for v in references):
                raise RuntimeError(_("Another VM uses a file belonging to this VM; refusing reset."))
            peer_xml = ET.parse(info["CfgFile"]).getroot()
            for element in peer_xml.iter():
                if ((element.tag.rsplit("}", 1)[-1] == "Image" and element.get("uuid", "").strip("{}").lower() == medium_id)
                        or (element.get("location") and reset_reference(element.get("location"), Path(info["CfgFile"]).parent, directory))):
                    raise RuntimeError(_("Another VM or its snapshot uses a file belonging to this VM; refusing reset."))
    elif backend == "vmware":
        config = pairs((directory / (spec["name"] + ".vmx")).read_text(encoding="utf-8"))
        attachments = [v for k, v in config.items() if re.fullmatch(r"(?:sata|scsi|ide|nvme)\d+:\d+\.filename", k.lower())]
        if any(v not in (image_name(backend), "installer.iso") for v in attachments):
            raise RuntimeError(_("The VMware VM has additional or foreign media; refusing reset."))
        disks = [v for k, v in config.items() if k.lower().endswith(".filename") and v.lower().endswith(".vmdk")]
        if disks != [image_name(backend)] or len(list(directory.glob("*.vmdk"))) != 1:
            raise RuntimeError(_("The VM has additional disks or unexpected disk files; refusing reset."))
        for path in directory.rglob("*"):
            if path.suffix.lower() in (".vmsn", ".vmss") or (snapshot["state"] == "shut off" and path.suffix.lower() in (".lck", ".vmem")):
                raise RuntimeError(_("The VM has a snapshot, saved state or lock; refusing reset."))
            if path.suffix.lower() == ".vmsd" and re.search(r'snapshot\d+\.', path.read_text(encoding="utf-8")):
                raise RuntimeError(_("The VM has snapshots; refusing reset."))
        with (directory / image_name(backend)).open("rb") as stream:
            descriptor = stream.read(1024**2).decode("utf-8", errors="ignore")
        if 'parentFileNameHint' in descriptor or not re.search(r'parentCID\s*=\s*ffffffff', descriptor, re.I):
            raise RuntimeError(_("The VMware disk has a backing image or unverified origin; refusing reset."))
        extents = re.findall(r'^\s*(?:RW|RDONLY|NOACCESS)\s+\d+\s+\S+\s+"([^"]+)"', descriptor, re.M)
        if extents != [image_name(backend)]:
            raise RuntimeError(_("The VMware disk references additional or unverified files; refusing reset."))
        peers = set(directory.parent.rglob("*.vmx"))
        peers.update(Path(p) for p in run([spec["toolPath"], "list"]).splitlines()[1:])
        peer_disks = set(directory.parent.rglob("*.vmdk"))
        for peer in peers:
            if peer.resolve() == (directory / (spec["name"] + ".vmx")).resolve():
                continue
            values = pairs(peer.read_text(encoding="utf-8"))
            references = [v for k, v in values.items() if k.lower().endswith(".filename") or k.lower() in ("nvram", "extendedconfigfile")]
            if any(reset_reference(v, peer.parent, directory) for v in references):
                raise RuntimeError(_("Another VM uses a file belonging to this VM; refusing reset."))
            peer_disks.update(Path(v) if Path(v).is_absolute() else peer.parent / v
                              for v in references if v.lower().endswith(".vmdk"))
        for disk in peer_disks:
            if disk.resolve() == (directory / image_name(backend)).resolve():
                continue
            with disk.open("rb") as stream:
                text = stream.read(1024**2).decode("utf-8", errors="ignore")
            for parent in re.findall(r'parentFileNameHint\s*=\s*"([^"]+)"', text):
                if reset_reference(parent, disk.parent, directory):
                    raise RuntimeError(_("Another VMware image depends on this disk; refusing reset."))
    else:
        base = "/nodes/" + spec["node"] + "/qemu/" + str(spec["vmid"])
        config = json.loads(run(["pvesh", "get", base + "/config", "--output-format", "json"]))
        if config.get("lock") or config.get("protection") or config.get("template"):
            raise RuntimeError(_("The Proxmox VM is locked, protected or a template; refusing reset."))
        snaps = json.loads(run(["pvesh", "get", base + "/snapshot", "--output-format", "json"]))
        if any(s["name"] != "current" for s in snaps):
            raise RuntimeError(_("The VM has snapshots; refusing reset."))
        hardware = json.loads((directory / "hardware.json").read_text(encoding="utf-8"))
        volumes = []
        for key in ("virtio0", "efidisk0"):
            volume = config[key].split(",")[0]
            storage, name = volume.split(":", 1)
            expected = hardware.get("diskVolume" if key == "virtio0" else "efiVolume", volume)
            if (volume != expected or storage != spec["storage"]
                    or not re.fullmatch(r"(?:" + str(spec["vmid"]) + r"/)?vm-" + str(spec["vmid"]) + r"-disk-\d+(?:\.(?:raw|qcow2|vmdk))?", name)):
                raise RuntimeError(_("The Proxmox VM disk is not a verified volume of this instance; refusing reset."))
            volumes.append(volume)
        media = []
        for key, value in config.items():
            if not re.fullmatch(r"(?:ide|sata|scsi|virtio|efidisk|tpmstate|unused)\d+", key) or key in ("virtio0", "efidisk0"):
                continue
            if key != "ide2" or value.split(",")[0] not in ("none", hardware.get("isoVolume")):
                raise RuntimeError(_("The Proxmox VM has an additional disk or foreign medium; refusing reset."))
        if marker.get("method") == "iso":
            volume, path = proxmox_iso_medium(spec)
            if path.exists():
                if path.is_symlink() or hardware.get("isoVolume") != volume:
                    raise RuntimeError(_("The managed ISO is not verified; refusing reset."))
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != marker["sha256"]:
                        raise RuntimeError(_("The managed ISO has different content; refusing reset."))
                media.append(str(path))
                volumes.append(volume)
        for peer in proxmox_registry():
            if peer.get("type") not in ("qemu", "lxc") or (peer.get("node") == spec["node"] and peer.get("vmid") == spec["vmid"] and peer.get("type") == "qemu"):
                continue
            peer_base = "/nodes/" + peer["node"] + "/" + peer["type"] + "/" + str(peer["vmid"])
            paths = [peer_base + "/config"]
            snapshots = json.loads(run(["pvesh", "get", peer_base + "/snapshot", "--output-format", "json"]))
            for snap in snapshots:
                if snap["name"] != "current":
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", snap["name"]):
                        raise RuntimeError(_("Cannot safely verify another VM's snapshot; refusing reset."))
                    paths.append(peer_base + "/snapshot/" + snap["name"] + "/config")
            for path in paths:
                info = json.loads(run(["pvesh", "get", path, "--output-format", "json"]))
                if any(v in re.split(r"[,;=\s]+", str(value)) for v in volumes for value in info.values()):
                    raise RuntimeError(_("Another VM, container or snapshot uses the volume or ISO; refusing reset."))
        result.update(volumes=volumes, media=media)
    return result


def reset_vm(spec, backend, directory, *, apply):
    plan = reset_checks(spec, backend, directory)
    if not apply:
        return {**plan, "readOnly": True}
    if plan["state"] == "running":
        if backend == "virtualbox":
            run([spec["toolPath"], "controlvm", spec["uuid"], "acpipowerbutton"])
        elif backend == "proxmox":
            run(["qm", "shutdown", str(spec["vmid"]), "--timeout", "120", "--forceStop", "0"], timeout=150)
        else:
            run([spec["toolPath"], "stop", str(directory / (spec["name"] + ".vmx")), "soft"], timeout=150)
        deadline = time.monotonic() + 120
        while probe(spec, backend, directory)["state"] != "shut off":
            if time.monotonic() >= deadline:
                raise RuntimeError(_("The VM did not shut down gracefully; reset will delete nothing."))
            time.sleep(2)
    final = reset_checks(spec, backend, directory)
    if final["state"] != "shut off" or any(final.get(k) != plan.get(k) for k in ("directory", "uuid", "disk", "volumes", "media")):
        raise RuntimeError(_("The VM or its resources changed during reset; nothing will be deleted."))
    if backend == "virtualbox":
        run([spec["toolPath"], "unregistervm", spec["uuid"], "--delete"])
    elif backend == "proxmox":
        run(["qm", "destroy", str(spec["vmid"]), "--purge", "1"], timeout=180)
        contents = json.loads(run(["pvesh", "get", "/nodes/" + spec["node"] + "/storage/" + spec["storage"] + "/content", "--output-format", "json"]))
        if any(item.get("volid") in final["volumes"] for item in contents):
            raise RuntimeError(_("The VM was removed, but its volume remains; keeping records for manual inspection."))
        for media in final["media"]:
            Path(media).unlink()
    if directory.exists():
        reset_tree(directory)
        shutil.rmtree(directory)
    if probe(spec, backend, directory)["state"] != "absent" or directory.exists():
        raise RuntimeError(_("Could not confirm removal of the VM or its directory."))
    return {"removed": spec["uuid"], "directory": str(directory)}


def dispatch(request):
    spec, backend, action = request["spec"], request["backend"], request["action"]
    if action not in ("probe", "probe-many", "plan", "prepare", "upload", "create", "start", "inventory", "installer-check", "boot-disk", "reset"):
        raise ValueError(_("Disallowed operation."))
    if action == "reset" and (request.get("allowReset") is not True or type(request.get("apply")) is not bool):
        raise ValueError(_("Reset requires explicit permission and an unambiguous apply mode."))
    if action == "probe-many":
        specs = request["specs"]
        if not isinstance(specs, list) or not 1 <= len(specs) <= 128:
            raise ValueError(_("Bulk status requires 1–128 VM declarations."))
        results, query_results, checked_tools = [], {}, set()
        for candidate in specs:
            try:
                directory = host_check(candidate, backend, checked_tools)
                results.append(probe(candidate, backend, directory, query_results))
            except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
                results.append({"error": str(error)})
        return results
    directory = host_check(spec, backend)
    if action == "reset":
        return reset_vm(spec, backend, directory, apply=request["apply"])
    if action == "probe":
        return probe(spec, backend, directory)
    if action == "inventory":
        return inventory(spec, backend, directory, request.get("declared", []))
    method = request.get("method", "image")
    if method not in ("iso", "image"):
        raise ValueError(_("Deployment method must be iso or image."))
    if action in ("plan", "prepare"):
        absent(spec, backend, directory)
        free = shutil.disk_usage(directory.parent).free
        required = request.get("imageBytes", 0) if backend == "proxmox" else max(
            request.get("imageBytes", 0), spec["diskGiB"] * 1024 ** 3)
        if method == "iso" and backend != "proxmox":
            required = request.get("imageBytes", 0) + spec["diskGiB"] * 1024**3
        if method == "iso" and backend == "vmware":
            executable = "vmware-vdiskmanager.exe" if spec["hostPlatform"] == "windows" else "vmware-vdiskmanager"
            manager = str(Path(spec["toolPath"]).with_name(executable))
            if not Path(manager).is_file() and not shutil.which(manager):
                raise RuntimeError(_("ISO installation requires vmware-vdiskmanager next to vmrun."))
        if method == "iso" and backend == "proxmox":
            _volume, iso_path = proxmox_iso_medium(spec)
            if iso_path.exists():
                raise RuntimeError(_("The target's managed ISO already exists; nothing will be overwritten."))
            if shutil.disk_usage(iso_path.parent).free < request.get("imageBytes", 0):
                raise RuntimeError(_("There is not enough free space in the Proxmox ISO storage."))
        if free < required:
            raise RuntimeError(_("There is not enough storage space for the image and declared VM capacity."))
        if action == "prepare":
            if not re.fullmatch(r"[a-f0-9]{64}", request["sha256"]):
                raise ValueError(_("The image SHA256 is missing."))
            private_directory(directory)
            with (directory / "nixberry.json").open("x", encoding="utf-8") as stream:
                json.dump({"spec": spec, "sha256": request["sha256"], "imageBytes": request["imageBytes"], "method": method}, stream)
        return {"directory": str(directory), "freeBytes": free, "requiredBytes": required, "state": "absent",
                "networkAdapters": network_adapters(spec, backend)}
    if owned(directory, spec).get("method", "image") != method:
        raise RuntimeError(_("The method differs from the created VM; use the original --method."))
    if action == "installer-check":
        return installer_media(spec, backend, directory)
    if action == "boot-disk":
        installer_media(spec, backend, directory, detach=True)
    if action == "upload":
        marker = owned(directory, spec)
        image = directory / media_name(backend, marker)
        digest, size = hashlib.sha256(), 0
        # Exclusive creation prevents overwrites even after an interrupted transfer.
        with image.open("xb") as stream:
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                size += len(chunk)
                if size > marker["imageBytes"]:
                    raise RuntimeError(_("The transfer exceeded the declared length."))
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != marker["sha256"] or size != marker["imageBytes"]:
            raise RuntimeError(_("The image transfer is incomplete or corrupted."))
        return {"sha256": digest.hexdigest(), "bytes": size}
    if action == "create":
        return create(spec, backend, directory)
    snapshot = probe(spec, backend, directory)
    if snapshot["state"] != "shut off":
        raise RuntimeError(_("Starting requires an existing powered-off VM with a verified owner."))
    if backend == "virtualbox":
        run([spec["toolPath"], "startvm", spec["uuid"], "--type", "headless"])
    elif backend == "proxmox":
        run(["qm", "start", str(spec["vmid"])])
    else:
        run([spec["toolPath"], "start", str(directory / (spec["name"] + ".vmx")), "nogui"],
            breakaway=spec["hostPlatform"] == "windows")
    return {"started": True}


def entry(request):
    try:
        print(json.dumps({"ok": True, "result": dispatch(request)}, ensure_ascii=True))
    except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=True))
        raise SystemExit(1)
