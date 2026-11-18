"""VirtualBox host operations and resource checks."""

from messages import _
import os
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET


from . import common

BACKEND = "virtualbox"


def registry(spec):
    machines = []
    for line in common.run([spec["toolPath"], "list", "vms"]).splitlines():
        match = re.fullmatch(r'"(.*)" \{([a-fA-F0-9-]+)\}', line)
        if not match:
            raise RuntimeError(_("Unknown VBoxManage list vms output."))
        machines.append({"name": match[1], "uuid": match[2].lower()})
    return machines


def guest_properties(text):
    # VirtualBox 7.2 normally prints a table; older versions print Name/value rows.
    properties = {}
    for line in text.splitlines():
        legacy = re.match(r"^Name: (/VirtualBox/[^,]+), value: ([^,]*)", line)
        table = re.match(r"^(/VirtualBox/\S+)\s*=\s*'([^']*)'\s+@", line)
        match = legacy or table
        if match:
            properties[match[1]] = match[2]
    return properties


def adapter_macs(spec):
    observed = []
    for vm in registry(spec):
        info = common.pairs(
            common.run(
                [spec["toolPath"], "showvminfo", vm["uuid"], "--machinereadable"]
            )
        )
        observed += [
            value for key, value in info.items() if re.fullmatch(r"macaddress\d+", key)
        ]

    return observed


def check_absent(spec, directory):
    if any(
        vm["uuid"] == spec["uuid"] or vm["name"] == spec["name"]
        for vm in registry(spec)
    ):
        raise RuntimeError(_("The VirtualBox VM name or UUID is already in use."))
    adapters = re.findall(
        r"^Name:\s+(.+)$", common.run([spec["toolPath"], "list", "bridgedifs"]), re.M
    )
    if any(
        item["bridge"] not in [adapter.strip() for adapter in adapters]
        for item in common.network_adapters(spec, BACKEND)
    ):
        raise RuntimeError(_("The declared VirtualBox bridge adapter does not exist."))


def network_matches(info, spec):
    expected = common.network_adapters(spec, BACKEND)
    enabled = {
        int(key[3:])
        for key, value in info.items()
        if re.fullmatch(r"nic\d+", key) and value not in ("none", "null")
    }
    if enabled != set(range(1, len(expected) + 1)):
        return False
    return all(
        info.get(f"nic{i}") == "bridged"
        and common.compact_mac(info.get(f"macaddress{i}", ""))
        == common.compact_mac(item["mac"])
        and info.get(f"bridgeadapter{i}") == item["bridge"]
        for i, item in enumerate(expected, 1)
    )


def probe(spec, directory, inventory=None):
    machines = common.read_once(
        inventory, (BACKEND, spec["toolPath"]), lambda: registry(spec)
    )
    matches = [
        v for v in machines if v["uuid"] == spec["uuid"] or v["name"] == spec["name"]
    ]
    if not matches:
        return {"state": "absent", "addresses": []}
    if len(matches) != 1 or matches[0] != {"name": spec["name"], "uuid": spec["uuid"]}:
        raise RuntimeError(_("The VirtualBox VM identity does not match."))
    common.owned(directory, spec)
    info = common.pairs(
        common.run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"])
    )
    marker = common.run(
        [spec["toolPath"], "getextradata", spec["uuid"], "nixberry/owner"]
    )
    disk = str(directory / common.image_name(BACKEND))
    if (
        marker != "Value: " + spec["ownerId"]
        or not network_matches(info, spec)
        or info.get("firmware", "").lower() not in ("efi", "efi64")
        or os.path.normcase(os.path.normpath(info.get("SATA-0-0", "")))
        != os.path.normcase(os.path.normpath(disk))
    ):
        raise RuntimeError(
            _("VirtualBox owner, disk, firmware or network does not match.")
        )
    values = []
    state = info["VMState"]
    if state == "running":
        properties = guest_properties(
            common.run([spec["toolPath"], "guestproperty", "enumerate", spec["uuid"]])
        )
        for key, value in properties.items():
            if (
                re.fullmatch(r"/VirtualBox/GuestInfo/Net/\d+/MAC", key)
                and value.replace(":", "").lower()
                == spec["mac"].replace(":", "").lower()
            ):
                address = properties.get(key.removesuffix("/MAC") + "/V4/IP")
                if address:
                    values.append(address)
    return {
        "state": "shut off" if state == "poweroff" else state,
        "addresses": common.safe_addresses(values),
    }


def create(spec, directory, marker, image, installing):
    if any(
        vm["uuid"] == spec["uuid"] or vm["name"] == spec["name"]
        for vm in registry(spec)
    ):
        raise RuntimeError(_("The VirtualBox VM already exists."))
    tool, identity = spec["toolPath"], spec["uuid"]
    common.run(
        [
            tool,
            "createvm",
            "--name",
            spec["name"],
            "--uuid",
            identity,
            "--ostype",
            "Linux_64",
            "--basefolder",
            str(directory),
            "--register",
        ]
    )
    common.run([tool, "setextradata", identity, "nixberry/owner", spec["ownerId"]])
    common.run(
        [
            tool,
            "modifyvm",
            identity,
            "--memory",
            str(spec["memoryMiB"]),
            "--cpus",
            str(spec["vcpus"]),
            # VirtualBox EFI reports SMBIOS 2.5; Linux does not reverse this UUID as for 2.6+.
            "--hardware-uuid",
            spec["uuid"],
            "--system-uuid-le",
            "off",
            "--firmware",
            "efi",
            "--ioapic",
            "on",
            "--boot1",
            "disk",
            "--boot2",
            "none",
            "--boot3",
            "none",
            "--boot4",
            "none",
            *[
                argument
                for i, adapter in enumerate(common.network_adapters(spec, BACKEND), 1)
                for argument in (
                    f"--nic{i}",
                    "bridged",
                    f"--bridge-adapter{i}",
                    adapter["bridge"],
                    f"--nic-type{i}",
                    "82540EM",
                    f"--mac-address{i}",
                    adapter["mac"].replace(":", ""),
                    f"--cable-connected{i}",
                    "on",
                )
            ],
        ]
    )
    common.run(
        [
            tool,
            "storagectl",
            identity,
            "--name",
            "SATA",
            "--add",
            "sata",
            "--controller",
            "IntelAhci",
            "--portcount",
            "2" if installing else "1",
            "--bootable",
            "on",
        ]
    )
    if installing:
        disk = directory / common.image_name(BACKEND)
        common.run(
            [
                tool,
                "createmedium",
                "disk",
                "--filename",
                str(disk),
                "--size",
                str(spec["diskGiB"] * 1024),
                "--format",
                "VDI",
            ]
        )
        common.run(
            [
                tool,
                "storageattach",
                identity,
                "--storagectl",
                "SATA",
                "--port",
                "1",
                "--device",
                "0",
                "--type",
                "dvddrive",
                "--medium",
                str(image),
            ]
        )
        common.run([tool, "modifyvm", identity, "--boot1", "dvd", "--boot2", "disk"])
        image = disk
    # Copies of the same VDI may share a media UUID. Assign this new instance's
    # reserved identity before attaching its disk for the first time.
    common.run(
        [
            tool,
            "storageattach",
            identity,
            "--storagectl",
            "SATA",
            "--port",
            "0",
            "--device",
            "0",
            "--type",
            "hdd",
            "--medium",
            str(image),
            "--setuuid",
            spec["uuid"],
        ]
    )


def inventory(spec, directory, declared):
    machines, images, errors, references = [], [], [], {}
    tool = spec["toolPath"]
    for vm in registry(spec):
        row = {
            **vm,
            "classification": "unknown",
            "allocatedBytes": None,
            "declared": any(s["uuid"] == vm["uuid"] for s in declared),
        }
        machines.append(row)
        try:
            info = common.pairs(
                common.run([tool, "showvminfo", vm["uuid"], "--machinereadable"])
            )
            row["state"] = info["VMState"]
            # XML also includes snapshot settings, not just the currently attached disk.
            xml = ET.parse(info["CfgFile"]).getroot()
            for element in xml.iter():
                if element.tag.rsplit("}", 1)[-1] == "Image":
                    identity = element.get("uuid", "").strip("{}").lower()
                    references.setdefault(identity, []).append(
                        "VM/snapshot:" + vm["name"]
                    )
        except (OSError, RuntimeError, KeyError, ET.ParseError) as error:
            errors.append(vm["name"] + ": " + str(error))
    text = common.run([tool, "list", "hdds"])
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
            size = common.file_info(path)
            capacity = re.match(r"(\d+) MBytes", info.get("Capacity", ""))
            images.append(
                {
                    **size,
                    "uuid": identity,
                    "capacityBytes": int(capacity[1]) * 1024 * 1024
                    if capacity
                    else None,
                }
            )
            if info.get("In use by VMs"):
                references.setdefault(identity, []).append("VirtualBox:In use by VMs")
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            errors.append("VirtualBox medium: " + str(error))
    # A backing disk is still needed by unattached differencing images.
    for child, parent in parents.items():
        references.setdefault(parent, []).append("backing:" + child)
    by_id = {image["uuid"]: image for image in images}
    for row in machines:
        identifiers = {
            identity
            for identity, reasons in references.items()
            if "VM/snapshot:" + row["name"] in reasons
        }
        pending = list(identifiers)
        while pending:
            parent = parents.get(pending.pop())
            if parent and parent not in identifiers:
                identifiers.add(parent)
                pending.append(parent)
        if all(identity in by_id for identity in identifiers):
            row["allocatedBytes"] = sum(
                by_id[identity]["allocatedBytes"] for identity in identifiers
            )
        row["classification"] = (
            "protected"
            if row.get("state") == "running" or row["declared"]
            else "unknown"
            if errors
            else "review"
        )
    for image in images:
        image["protectedBy"] = references.get(image["uuid"], [])
        image["classification"] = (
            "protected" if image["protectedBy"] else "unknown" if errors else "review"
        )
    return {
        "machines": machines,
        "images": images,
        "errors": errors,
        "note": _(
            "SSH user's VirtualBox registry, including media and snapshot references. review requires checking usage outside this registry. Do not add shared disk sizes across VMs."
        ),
    }


def installer_media(spec, directory, marker, detach):
    path = str(directory / "installer.iso")
    info = common.pairs(
        common.run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"])
    )
    if info.get("SATA-1-0") != path:
        raise RuntimeError(_("The VM is not using the expected ISO."))
    if detach:
        common.run(
            [
                spec["toolPath"],
                "storageattach",
                spec["uuid"],
                "--storagectl",
                "SATA",
                "--port",
                "1",
                "--device",
                "0",
                "--type",
                "dvddrive",
                "--medium",
                "none",
            ]
        )
        common.run(
            [
                spec["toolPath"],
                "modifyvm",
                spec["uuid"],
                "--boot1",
                "disk",
                "--boot2",
                "none",
            ]
        )


def reset_checks(spec, directory, marker, snapshot, result):
    def attachments(info):
        controllers = [
            v for k, v in info.items() if re.fullmatch(r"storagecontrollername\d+", k)
        ]
        return [
            v
            for k, v in info.items()
            if any(re.fullmatch(re.escape(c) + r"-\d+-\d+", k) for c in controllers)
            and v not in ("none", "emptydrive", "")
        ]

    details = common.pairs(
        common.run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"])
    )
    # createvm composes <basefolder>/<name>/<name>.vbox, including when
    # the supplied base folder already has the instance's name.
    if (
        not details.get("CfgFile")
        or Path(details["CfgFile"]).resolve()
        != (directory / spec["name"] / (spec["name"] + ".vbox")).resolve()
    ):
        raise RuntimeError(
            _(
                "The VirtualBox VM configuration is outside its dedicated directory; refusing reset."
            )
        )
    if details.get("SnapshotCount", "0") != "0":
        raise RuntimeError(_("The VM has snapshots; refusing reset."))
    allowed = {
        str(directory / common.image_name(BACKEND)),
        str(directory / "installer.iso"),
    }
    if any(v not in allowed for v in attachments(details)):
        raise RuntimeError(_("The VM has additional or foreign media; refusing reset."))

    def medium_fields(text):
        result = {}
        for line in text.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                result[key.strip()] = value.strip()
        return result

    medium = medium_fields(
        common.run(
            [
                spec["toolPath"],
                "showmediuminfo",
                "disk",
                str(directory / common.image_name(BACKEND)),
            ]
        )
    )
    medium_id = str(uuid.UUID(medium["UUID"]))
    if medium.get("Parent UUID", "").lower() not in (
        "base",
        "00000000-0000-0000-0000-000000000000",
    ):
        raise RuntimeError(
            _("The disk has a backing image or unverified origin; refusing reset.")
        )
    usage = medium.get("In use by VMs", "")
    users = re.findall(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", usage)
    if any(identity.lower() != spec["uuid"] for identity in users):
        raise RuntimeError(
            _("The disk is used by another VM or snapshot; refusing reset.")
        )
    for block in re.split(r"\n\s*\n", common.run([spec["toolPath"], "list", "hdds"])):
        if medium_fields(block).get("Parent UUID", "").lower() == medium_id:
            raise RuntimeError(
                _("A differencing image depends on this disk; refusing reset.")
            )
    for peer in registry(spec):
        if peer["uuid"] == spec["uuid"]:
            continue
        info = common.pairs(
            common.run(
                [spec["toolPath"], "showvminfo", peer["uuid"], "--machinereadable"]
            )
        )
        references = attachments(info) + [
            info[k] for k in ("CfgFile", "SnapFldr", "NvramFile") if info.get(k)
        ]
        if any(
            common.reset_reference(v, directory.parent, directory) for v in references
        ):
            raise RuntimeError(
                _("Another VM uses a file belonging to this VM; refusing reset.")
            )
        peer_xml = ET.parse(info["CfgFile"]).getroot()
        for element in peer_xml.iter():
            if (
                element.tag.rsplit("}", 1)[-1] == "Image"
                and element.get("uuid", "").strip("{}").lower() == medium_id
            ) or (
                element.get("location")
                and common.reset_reference(
                    element.get("location"), Path(info["CfgFile"]).parent, directory
                )
            ):
                raise RuntimeError(
                    _(
                        "Another VM or its snapshot uses a file belonging to this VM; refusing reset."
                    )
                )

    return result


def shutdown(spec, directory):
    common.run([spec["toolPath"], "controlvm", spec["uuid"], "acpipowerbutton"])


def remove(spec, directory, final):
    common.run([spec["toolPath"], "unregistervm", spec["uuid"], "--delete"])


def start(spec, directory):
    common.run([spec["toolPath"], "startvm", spec["uuid"], "--type", "headless"])


def check_iso(spec, image_bytes):
    pass
