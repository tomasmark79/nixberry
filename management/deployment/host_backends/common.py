"""Shared host identity, ownership, filesystem and command safety checks."""

from messages import _, forward_stderr
import csv
import ipaddress
import json
import locale
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import uuid
from vm_network import adapters as network_adapters, mac as compact_mac


def run(argv, *, timeout=120, breakaway=False):
    # Windows SSH terminates job processes when the session closes. Hypervisor
    # startup must escape that job so a running VM survives the RPC response.
    options = {"creationflags": 0x01000000} if breakaway and os.name == "nt" else {}
    result = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "LC_ALL": "C"},
        **options,
    )

    def decode(value):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode(locale.getpreferredencoding(False), errors="replace")

    stdout, stderr = decode(result.stdout), decode(result.stderr)
    if result.returncode:
        raise RuntimeError(
            " ".join(argv[:2]) + ": " + (stderr.strip() or stdout.strip())
        )
    forward_stderr(stderr)
    return stdout.strip()


def private_directory(directory):
    directory.mkdir(mode=0o700)
    if os.name == "nt":
        # An elevated SSH session may assign ownership to Administrators. The GUI
        # of the same account has that group disabled in its restricted token;
        # explicitly grant the account SID without granting other users access.
        rows = list(
            csv.reader(run(["whoami.exe", "/user", "/fo", "csv", "/nh"]).splitlines())
        )
        if (
            len(rows) != 1
            or len(rows[0]) != 2
            or not re.fullmatch(r"S-1-\d+(?:-\d+)+", rows[0][1])
        ):
            raise RuntimeError(
                _("Cannot verify the user SID for the private VM directory.")
            )
        run(
            [
                "icacls.exe",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                "*" + rows[0][1] + ":(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ]
        )


def validate(spec, backend):
    if backend not in ("proxmox", "virtualbox", "vmware"):
        raise ValueError(_("Unsupported backend."))
    network_adapters(spec, backend)
    if spec["hostPlatform"] not in ("linux", "windows") or (
        backend == "proxmox" and spec["hostPlatform"] != "linux"
    ):
        raise ValueError(_("Unsupported host system."))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", spec["hostName"]):
        raise ValueError(_("Invalid host hostname."))
    if not re.fullmatch(
        r"(?!(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$)[a-z][a-z0-9-]{0,62}", spec["name"]
    ):
        raise ValueError(
            _(
                "Invalid VM name: use 1–63 lowercase letters, digits and hyphens, starting with a letter; reserved Windows device names are not allowed."
            )
        )
    identity = str(uuid.UUID(spec["uuid"]))
    if identity != spec["uuid"] or spec["ownerId"] != "nixberry-" + identity:
        raise ValueError(_("Invalid UUID or owner marker."))
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", spec["mac"]):
        raise ValueError(_("Invalid MAC address."))
    if int(spec["mac"][:2], 16) & 1:
        raise ValueError(_("Multicast MAC addresses are not allowed."))
    if backend == "vmware" and (
        not spec["mac"].lower().startswith("00:50:56:")
        or int(spec["mac"].split(":")[3], 16) > 0x3F
    ):
        raise ValueError(
            _(
                "A static VMware MAC must be in the range 00:50:56:00:00:00–00:50:56:3f:ff:ff."
            )
        )
    for key, minimum, maximum in (
        ("memoryMiB", 1024, 1048576),
        ("vcpus", 1, 1024),
        ("diskGiB", 8, 1048576),
    ):
        if type(spec[key]) is not int or not minimum <= spec[key] <= maximum:
            raise ValueError(_("Invalid capacity: {key}").format(key=key))
    if backend == "proxmox":
        if type(spec["vmid"]) is not int or not 100 <= spec["vmid"] <= 999999999:
            raise ValueError(_("Invalid VMID."))
        for key in ("storage", "bridge", "node"):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", spec[key]):
                raise ValueError(_("Invalid Proxmox resource: {key}").format(key=key))
        if not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.-]*", spec.get("isoStorage", "local")
        ):
            raise ValueError(_("Invalid Proxmox ISO storage."))
    elif backend == "virtualbox":
        if (
            not isinstance(spec["bridge"], str)
            or not spec["bridge"]
            or any(c in spec["bridge"] for c in "\r\n\x00")
        ):
            raise ValueError(_("VirtualBox requires an existing bridge adapter."))
    if backend != "proxmox" and (
        not isinstance(spec["toolPath"], str) or not spec["toolPath"]
    ):
        raise ValueError(_("The hypervisor tool path is missing."))
    if not isinstance(spec["storageDirectory"], str) or any(
        c in spec["storageDirectory"] for c in "\r\n\x00"
    ):
        raise ValueError(_("Invalid storage path."))
    if backend == "proxmox" and "," in spec["storageDirectory"]:
        raise ValueError(_("The Proxmox import path must not contain a comma."))


def host_check(spec, backend, checked_tools=None):
    validate(spec, backend)
    if socket.gethostname().lower() != spec["hostName"].lower():
        raise RuntimeError(_("The host hostname does not match the declaration."))
    expected = "Windows" if spec["hostPlatform"] == "windows" else "Linux"
    if platform.system() != expected:
        raise RuntimeError(
            _("The host operating system does not match the declaration.")
        )
    root = Path(spec["storageDirectory"])
    if not root.is_absolute() or root == Path(root.anchor) or not root.is_dir():
        raise RuntimeError(
            _(
                "The dedicated storageDirectory must be an existing absolute directory outside the drive root."
            )
        )
    for path in (root, *root.parents):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise RuntimeError(_("The storage path must not contain a symbolic link."))
    if backend == "proxmox":
        if socket.gethostname().split(".")[0] != spec["node"]:
            raise RuntimeError(
                _("SSH must connect directly to the declared Proxmox node.")
            )
    tool = (backend, spec.get("toolPath"), spec.get("node"))
    if checked_tools is None or tool not in checked_tools:
        if backend == "proxmox":
            run(["pveversion"])
        else:
            run(
                [spec["toolPath"], "--version"]
                if backend == "virtualbox"
                else [spec["toolPath"], "list"]
            )
        if checked_tools is not None:
            checked_tools.add(tool)
    return root / spec["name"]


def owned(directory, spec):
    if (
        directory.is_symlink()
        or (hasattr(directory, "is_junction") and directory.is_junction())
        or not directory.is_dir()
    ):
        raise RuntimeError(_("The VM directory is missing or is a link."))
    marker = directory / "nixberry.json"
    if marker.is_symlink():
        raise RuntimeError(_("The owner marker must not be a link."))
    data = json.loads(marker.read_text(encoding="utf-8"))
    if data["spec"] != spec:
        raise RuntimeError(
            _(
                "The saved VM declaration differs from the current one; nothing will be overwritten."
            )
        )
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
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        result[key] = value
    return result


def safe_addresses(values):
    found = set()
    networks = [
        ipaddress.ip_network(net)
        for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ]
    for value in values:
        try:
            address = ipaddress.ip_interface(value).ip
            if (
                address.version == 4
                and str(address) != "192.168.79.1"
                and any(address in n for n in networks)
            ):
                found.add(str(address))
        except ValueError:
            pass
    return sorted(found)


def image_name(backend):
    return (
        "system." + {"proxmox": "qcow2", "virtualbox": "vdi", "vmware": "vmdk"}[backend]
    )


def media_name(backend, marker):
    return (
        "installer.iso"
        if marker.get("method", "image") == "iso"
        else image_name(backend)
    )


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
    return {
        "path": str(path),
        "fileBytes": stat.st_size,
        "logicalBytes": stat.st_size,
        "allocatedBytes": allocation,
        "modified": stat.st_mtime,
    }


def reset_tree(directory, hardlink=None):
    # Includes Windows junctions and hard links to files outside the owned VM.
    pending = [directory]
    while pending:
        path = pending.pop()
        data = path.lstat()
        allowed_link = (
            hardlink is not None
            and path == directory / "installer.iso"
            and data.st_nlink == 2
            and not hardlink.is_symlink()
            and os.path.samefile(path, hardlink)
        )
        if (
            path.is_symlink()
            or path.is_mount()
            or getattr(data, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or (stat.S_ISREG(data.st_mode) and data.st_nlink != 1 and not allowed_link)
        ):
            raise RuntimeError(
                _(
                    "The VM directory contains a link, mount point or shared file; refusing reset."
                )
            )
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


def read_once(inventory, key, query):
    if inventory is None:
        return query()
    if key not in inventory:
        inventory[key] = query()
    return inventory[key]
