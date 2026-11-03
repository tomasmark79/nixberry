"""VMware host operations and resource checks."""

from messages import _
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


from . import common

BACKEND = "vmware"


def bios_uuid(spec):
    # SMBIOS 2.6+ stores the first three UUID fields in little-endian order.
    values = [f"{byte:02x}" for byte in uuid.UUID(spec["uuid"]).bytes_le]
    # VMX requires a hyphen between bytes eight and nine; otherwise VMware
    # rejects the UUID and assigns a different one, breaking declared identity.
    return " ".join(values[:8]) + "-" + " ".join(values[8:])


def vmx_text(spec):
    values = {
        ".encoding": "UTF-8",
        "config.version": "8",
        "virtualHW.version": "19",
        "displayName": spec["name"],
        "annotation": spec["ownerId"],
        "guestOS": "otherlinux-64",
        "firmware": "efi",
        "uefi.secureBoot.enabled": "FALSE",
        "memsize": str(spec["memoryMiB"]),
        "numvcpus": str(spec["vcpus"]),
        "uuid.bios": bios_uuid(spec),
        "uuid.action": "keep",
        "msg.autoAnswer": "TRUE",
        "sata0.present": "TRUE",
        "sata0:0.present": "TRUE",
        "sata0:0.fileName": "system.vmdk",
        "tools.syncTime": "FALSE",
        "pciBridge0.present": "TRUE",
        "pciBridge4.present": "TRUE",
        "pciBridge4.virtualDev": "pcieRootPort",
    }
    for index, adapter in enumerate(common.network_adapters(spec, "vmware")):
        prefix = f"ethernet{index}."
        values.update(
            {
                prefix + "present": "TRUE",
                prefix + "virtualDev": "e1000e",
                prefix + "connectionType": "nat"
                if adapter["vmnet"] == "nat"
                else "custom",
                prefix + "addressType": "static",
                prefix + "address": adapter["mac"],
                prefix + "startConnected": "TRUE",
            }
        )
        if adapter["vmnet"] != "nat":
            values[prefix + "vnet"] = adapter["vmnet"]
    return "\n".join(key + ' = "' + value + '"' for key, value in values.items()) + "\n"


def adapter_macs(spec):
    observed = []
    # Workstation has no global stopped-VM registry. Inspect its dedicated
    # storage and registered running paths; external stopped VMs remain an operator boundary.
    paths = set(Path(spec["storageDirectory"]).rglob("*.vmx"))
    paths.update(
        Path(path) for path in common.run([spec["toolPath"], "list"]).splitlines()[1:]
    )
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(_("VMX must not be a link."))
        info = common.pairs(path.read_text(encoding="utf-8"))
        observed += [
            value
            for key, value in info.items()
            if re.fullmatch(r"ethernet\d+\.address", key)
        ]

    return observed


def check_absent(spec, directory):
    # Workstation cannot inventory all stopped VMs. Reserve the name and
    # directory within the declared root and also check running VMs.
    for vmx in common.run([spec["toolPath"], "list"]).splitlines()[1:]:
        path = Path(vmx)
        if path.is_file():
            values = common.pairs(path.read_text(encoding="utf-8"))
            if (
                values.get("displayName") == spec["name"]
                or values.get("annotation") == spec["ownerId"]
            ):
                raise RuntimeError(_("The matching VMware VM is already running."))


def network_matches(info, spec):
    expected = common.network_adapters(spec, BACKEND)
    enabled = {
        int(match[1])
        for key, value in info.items()
        if (match := re.fullmatch(r"ethernet(\d+)\.present", key))
        and value.upper() == "TRUE"
    }
    if enabled != set(range(len(expected))):
        return False
    for i, item in enumerate(expected):
        prefix = f"ethernet{i}."
        if (
            common.compact_mac(info.get(prefix + "address", ""))
            != common.compact_mac(item["mac"])
            or info.get(prefix + "addressType") != "static"
            or info.get(prefix + "connectionType")
            != ("nat" if item["vmnet"] == "nat" else "custom")
            or (item["vmnet"] != "nat" and info.get(prefix + "vnet") != item["vmnet"])
        ):
            return False
    return True


def probe(spec, directory, inventory=None):
    vmx = directory / (spec["name"] + ".vmx")
    if not vmx.exists():
        return {"state": "absent", "addresses": []}
    common.owned(directory, spec)
    if vmx.is_symlink():
        raise RuntimeError(_("VMX must not be a link."))
    info = common.pairs(vmx.read_text(encoding="utf-8"))
    bios = bios_uuid(spec)
    if (
        info.get("annotation") != spec["ownerId"]
        or info.get("displayName") != spec["name"]
        or info.get("uuid.bios", "").lower().replace("-", " ").split()
        != bios.replace("-", " ").split()
        or not network_matches(info, spec)
        or info.get("firmware") != "efi"
        or info.get("sata0:0.fileName") != common.image_name(BACKEND)
    ):
        raise RuntimeError(
            _("The VMware configuration does not match the declaration.")
        )
    running = common.read_once(
        inventory,
        (BACKEND, spec["toolPath"], "running"),
        lambda: common.run([spec["toolPath"], "list"]).splitlines()[1:],
    )
    active = any(
        os.path.normcase(os.path.abspath(p)) == os.path.normcase(str(vmx))
        for p in running
    )
    values = []
    if active:
        try:
            # Without -wait, headless Workstation may fail even with running Tools.
            # A short timeout keeps status fast while a VM starts.
            values = [
                common.run(
                    [spec["toolPath"], "getGuestIPAddress", str(vmx), "-wait"],
                    timeout=5,
                )
            ]
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
    return {
        "state": "running" if active else "shut off",
        "addresses": common.safe_addresses(values),
    }


def create(spec, directory, marker, image, installing):
    if installing:
        executable = (
            "vmware-vdiskmanager.exe"
            if spec["hostPlatform"] == "windows"
            else "vmware-vdiskmanager"
        )
        manager = str(Path(spec["toolPath"]).with_name(executable))
        common.run(
            [
                manager,
                "-c",
                "-s",
                str(spec["diskGiB"]) + "GB",
                "-a",
                "lsilogic",
                "-t",
                "0",
                str(directory / common.image_name(BACKEND)),
            ],
            timeout=1800,
        )
    with (directory / (spec["name"] + ".vmx")).open(
        "x", encoding="utf-8", newline="\n"
    ) as stream:
        stream.write(vmx_text(spec))
        if installing:
            stream.write(
                'sata0:1.present = "TRUE"\nsata0:1.deviceType = "cdrom-image"\nsata0:1.fileName = "installer.iso"\nsata0:1.startConnected = "TRUE"\nbios.bootOrder = "cdrom,hdd"\n'
            )


def inventory(spec, directory, declared):
    machines, images, errors = [], [], []
    running = {
        os.path.normcase(os.path.abspath(path))
        for path in common.run([spec["toolPath"], "list"]).splitlines()[1:]
    }
    configs, references = set(running), {}

    # Scope is explicit: the entire dedicated directory, including stopped VMware VMs.
    def walk_error(error):
        errors.append(str(error))

    for base, dirs, files in os.walk(
        directory.parent, followlinks=False, onerror=walk_error
    ):
        dirs[:] = [
            d
            for d in dirs
            if not Path(base, d).is_symlink()
            and not (
                hasattr(Path(base, d), "is_junction") and Path(base, d).is_junction()
            )
        ]
        for name in files:
            path = Path(base, name)
            if name.endswith(
                (
                    ".qcow2",
                    ".vdi",
                    ".vmdk",
                    ".vhdx",
                    ".iso",
                    ".vmx",
                    ".vmsn",
                    ".vmem",
                    ".nvram",
                )
            ):
                try:
                    images.append(
                        {
                            **common.file_info(path),
                            "classification": "unknown",
                            "protectedBy": [],
                            "note": _(
                                "A disk, snapshot, backing image or recovery medium may be attached."
                            ),
                        }
                    )
                except OSError as error:
                    errors.append(str(error))
            if BACKEND == "vmware" and name.endswith(".vmx"):
                configs.add(os.path.normcase(os.path.abspath(path)))
    for config in sorted(configs):
        path = Path(config)
        try:
            info = common.pairs(path.read_text(encoding="utf-8"))
            known = any(s["ownerId"] == info.get("annotation") for s in declared)
            row = {
                "name": info.get("displayName"),
                "path": str(path),
                "state": "running" if config in running else "shut off",
                "declared": known,
                "classification": "protected"
                if known or config in running
                else "review",
                "allocatedBytes": 0,
            }
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
                        row["note"] = _(
                            "The VM has media outside its own directory; total allocation is unknown."
                        )
        except (OSError, ValueError, KeyError) as error:
            errors.append(str(path) + ": " + str(error))
    for image in images:
        reasons = references.get(os.path.normcase(os.path.abspath(image["path"])), [])
        image["protectedBy"].extend(reasons)
        if reasons:
            image["classification"] = "protected"
    return {
        "machines": machines,
        "images": images,
        "errors": errors,
        "note": _(
            "Inventory is read-only. VMware: running VMs and files in the dedicated directory, not all stopped VMs on the host. unknown is not a deletion candidate. Snapshots and saved states in each VM directory are also protected. On Windows, allocation is read through GetCompressedFileSizeW; logicalBytes is the file length."
        ),
    }


def installer_media(spec, directory, marker, detach):
    vmx = directory / (spec["name"] + ".vmx")
    text = vmx.read_text(encoding="utf-8")
    info = common.pairs(text)
    if (
        info.get("sata0:1.fileName") != "installer.iso"
        or info.get("sata0:1.present") != "TRUE"
    ):
        raise RuntimeError(_("The VM is not using the expected ISO."))
    if detach:
        text = text.replace(
            'sata0:1.present = "TRUE"', 'sata0:1.present = "FALSE"'
        ).replace('bios.bootOrder = "cdrom,hdd"', 'bios.bootOrder = "hdd"')
        vmx.write_text(text, encoding="utf-8")


def reset_checks(spec, directory, marker, snapshot, result):
    config = common.pairs(
        (directory / (spec["name"] + ".vmx")).read_text(encoding="utf-8")
    )
    attachments = [
        v
        for k, v in config.items()
        if re.fullmatch(r"(?:sata|scsi|ide|nvme)\d+:\d+\.filename", k.lower())
    ]
    if any(v not in (common.image_name(BACKEND), "installer.iso") for v in attachments):
        raise RuntimeError(
            _("The VMware VM has additional or foreign media; refusing reset.")
        )
    disks = [
        v
        for k, v in config.items()
        if k.lower().endswith(".filename") and v.lower().endswith(".vmdk")
    ]
    if (
        disks != [common.image_name(BACKEND)]
        or len(list(directory.glob("*.vmdk"))) != 1
    ):
        raise RuntimeError(
            _("The VM has additional disks or unexpected disk files; refusing reset.")
        )
    for path in directory.rglob("*"):
        if path.suffix.lower() in (".vmsn", ".vmss") or (
            snapshot["state"] == "shut off" and path.suffix.lower() in (".lck", ".vmem")
        ):
            raise RuntimeError(
                _("The VM has a snapshot, saved state or lock; refusing reset.")
            )
        if path.suffix.lower() == ".vmsd" and re.search(
            r"snapshot\d+\.", path.read_text(encoding="utf-8")
        ):
            raise RuntimeError(_("The VM has snapshots; refusing reset."))
    with (directory / common.image_name(BACKEND)).open("rb") as stream:
        descriptor = stream.read(1024**2).decode("utf-8", errors="ignore")
    if "parentFileNameHint" in descriptor or not re.search(
        r"parentCID\s*=\s*ffffffff", descriptor, re.I
    ):
        raise RuntimeError(
            _(
                "The VMware disk has a backing image or unverified origin; refusing reset."
            )
        )
    extents = re.findall(
        r'^\s*(?:RW|RDONLY|NOACCESS)\s+\d+\s+\S+\s+"([^"]+)"', descriptor, re.M
    )
    if extents != [common.image_name(BACKEND)]:
        raise RuntimeError(
            _(
                "The VMware disk references additional or unverified files; refusing reset."
            )
        )
    peers = set(directory.parent.rglob("*.vmx"))
    peers.update(
        Path(p) for p in common.run([spec["toolPath"], "list"]).splitlines()[1:]
    )
    peer_disks = set(directory.parent.rglob("*.vmdk"))
    for peer in peers:
        if peer.resolve() == (directory / (spec["name"] + ".vmx")).resolve():
            continue
        values = common.pairs(peer.read_text(encoding="utf-8"))
        references = [
            v
            for k, v in values.items()
            if k.lower().endswith(".filename")
            or k.lower() in ("nvram", "extendedconfigfile")
        ]
        if any(common.reset_reference(v, peer.parent, directory) for v in references):
            raise RuntimeError(
                _("Another VM uses a file belonging to this VM; refusing reset.")
            )
        peer_disks.update(
            Path(v) if Path(v).is_absolute() else peer.parent / v
            for v in references
            if v.lower().endswith(".vmdk")
        )
    for disk in peer_disks:
        if disk.resolve() == (directory / common.image_name(BACKEND)).resolve():
            continue
        with disk.open("rb") as stream:
            text = stream.read(1024**2).decode("utf-8", errors="ignore")
        for parent in re.findall(r'parentFileNameHint\s*=\s*"([^"]+)"', text):
            if common.reset_reference(parent, disk.parent, directory):
                raise RuntimeError(
                    _("Another VMware image depends on this disk; refusing reset.")
                )

    return result


def shutdown(spec, directory):
    common.run(
        [spec["toolPath"], "stop", str(directory / (spec["name"] + ".vmx")), "soft"],
        timeout=150,
    )


def remove(spec, directory, final):
    pass


def start(spec, directory):
    common.run(
        [spec["toolPath"], "start", str(directory / (spec["name"] + ".vmx")), "nogui"],
        breakaway=spec["hostPlatform"] == "windows",
    )


def check_iso(spec, image_bytes):
    executable = (
        "vmware-vdiskmanager.exe"
        if spec["hostPlatform"] == "windows"
        else "vmware-vdiskmanager"
    )
    manager = str(Path(spec["toolPath"]).with_name(executable))
    if not Path(manager).is_file() and not shutil.which(manager):
        raise RuntimeError(
            _("ISO installation requires vmware-vdiskmanager next to vmrun.")
        )
