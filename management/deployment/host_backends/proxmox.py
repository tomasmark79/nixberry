"""Proxmox host operations and resource checks."""

from messages import _
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import shutil


from . import common

BACKEND = "proxmox"


def registry():
    return json.loads(
        common.run(
            [
                "pvesh",
                "get",
                "/cluster/resources",
                "--type",
                "vm",
                "--output-format",
                "json",
            ]
        )
    )


def iso_medium(spec):
    storage = spec.get("isoStorage", "local")
    config = json.loads(
        common.run(["pvesh", "get", "/storage/" + storage, "--output-format", "json"])
    )
    if config.get("disable") or "iso" not in config.get("content", "").split(","):
        raise RuntimeError(
            _("Proxmox storage {storage} is not enabled for ISO content.").format(
                storage=storage
            )
        )
    status = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                "/nodes/" + spec["node"] + "/storage/" + storage + "/status",
                "--output-format",
                "json",
            ]
        )
    )
    if not status.get("active") or not status.get("enabled"):
        raise RuntimeError(
            _("ISO storage {storage} is inactive on the target node.").format(
                storage=storage
            )
        )
    volume = storage + ":iso/nixberry-" + spec["uuid"] + ".iso"
    path = Path(common.run(["pvesm", "path", volume]))
    if (
        not path.is_absolute()
        or not path.parent.is_dir()
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise RuntimeError(_("The managed ISO path is not a safe existing directory."))
    return volume, path


def adapter_macs(spec):
    observed = []
    for vm in registry():
        if vm.get("type") != "qemu":
            continue
        info = json.loads(
            common.run(
                [
                    "pvesh",
                    "get",
                    f"/nodes/{vm['node']}/qemu/{vm['vmid']}/config",
                    "--output-format",
                    "json",
                ]
            )
        )
        observed += [
            part.split("=", 1)[1]
            for key, value in info.items()
            if re.fullmatch(r"net\d+", key)
            for part in value.split(",")
            if re.fullmatch(r"[A-Za-z0-9]+=(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", part)
        ]

    return observed


def check_absent(spec, directory):
    if any(
        vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"]
        for vm in registry()
    ):
        raise RuntimeError(
            _("The Proxmox VMID or name is already in use in the cluster.")
        )
    storage = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                f"/nodes/{spec['node']}/storage/{spec['storage']}/status",
                "--output-format",
                "json",
            ]
        )
    )
    if not storage.get("active"):
        raise RuntimeError(_("The Proxmox storage is inactive."))
    required = spec["diskGiB"] * 1024**3 + 4 * 1024**2
    if storage.get("avail", 0) < required:
        raise RuntimeError(
            _("The Proxmox storage lacks space for the declared disk capacity and EFI.")
        )
    if any(
        not Path("/sys/class/net", item["bridge"], "bridge").is_dir()
        for item in common.network_adapters(spec, BACKEND)
    ):
        raise RuntimeError(_("The Proxmox bridge does not exist."))


def network_matches(info, spec):
    expected = common.network_adapters(spec, BACKEND)
    enabled = {key for key in info if re.fullmatch(r"net\d+", key)}
    if enabled != {f"net{i}" for i in range(len(expected))}:
        return False
    for i, item in enumerate(expected):
        fields = dict(
            part.split("=", 1) for part in info[f"net{i}"].split(",") if "=" in part
        )
        if (
            common.compact_mac(fields.get("virtio", ""))
            != common.compact_mac(item["mac"])
            or fields.get("bridge") != item["bridge"]
        ):
            return False
    return True


def probe(spec, directory, inventory=None):
    machines = common.read_once(inventory, (BACKEND, "cluster"), registry)
    matches = [
        vm
        for vm in machines
        if vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"]
    ]
    if matches and (
        len(matches) != 1
        or matches[0]["vmid"] != spec["vmid"]
        or matches[0].get("node") != spec["node"]
        or matches[0].get("type") != "qemu"
    ):
        raise RuntimeError(_("The Proxmox VM identity or node does not match."))
    # Cluster inventory may lag actual state after create/start.
    local = common.read_once(
        inventory,
        (BACKEND, spec["node"]),
        lambda: json.loads(
            common.run(
                [
                    "pvesh",
                    "get",
                    f"/nodes/{spec['node']}/qemu",
                    "--output-format",
                    "json",
                ]
            )
        ),
    )
    matches = [
        vm
        for vm in local
        if vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"]
    ]
    if not matches:
        return {"state": "absent", "addresses": []}
    if len(matches) != 1 or matches[0]["vmid"] != spec["vmid"]:
        raise RuntimeError(_("The Proxmox VM identity or node does not match."))
    common.owned(directory, spec)
    info = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                f"/nodes/{spec['node']}/qemu/{spec['vmid']}/config",
                "--output-format",
                "json",
            ]
        )
    )
    if (
        info.get("description") != spec["ownerId"]
        or info.get("name") != spec["name"]
        or "uuid=" + spec["uuid"] not in info.get("smbios1", "")
        or info.get("bios") != "ovmf"
        or not network_matches(info, spec)
    ):
        raise RuntimeError(
            _("The Proxmox configuration does not match the owner or hardware.")
        )
    binding = directory / "hardware.json"
    if binding.is_symlink():
        raise RuntimeError(_("The disk record must not be a link."))
    disk = json.loads(binding.read_text(encoding="utf-8"))["diskVolume"]
    if info.get("virtio0", "").split(",")[0] != disk:
        raise RuntimeError(
            _("The Proxmox system disk was changed outside the declared creation.")
        )
    current = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                f"/nodes/{spec['node']}/qemu/{spec['vmid']}/status/current",
                "--output-format",
                "json",
            ]
        )
    )
    state = current["status"]
    values = []
    if state == "running":
        try:
            network = json.loads(
                common.run(
                    ["qm", "guest", "cmd", str(spec["vmid"]), "network-get-interfaces"]
                )
            )
            for adapter in network:
                if adapter.get("hardware-address", "").lower() == spec["mac"].lower():
                    values.extend(
                        item["ip-address"] for item in adapter.get("ip-addresses", [])
                    )
        except RuntimeError:
            pass
    return {
        "state": "shut off" if state == "stopped" else state,
        "addresses": common.safe_addresses(values),
    }


def create(spec, directory, marker, image, installing):
    if any(
        vm["vmid"] == spec["vmid"] or vm.get("name") == spec["name"]
        for vm in registry()
    ):
        raise RuntimeError(_("The Proxmox VM already exists."))
    iso_volume = None
    if installing:
        iso_volume, iso_path = iso_medium(spec)
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
    common.run(
        [
            "qm",
            "create",
            str(spec["vmid"]),
            "--name",
            spec["name"],
            "--description",
            spec["ownerId"],
            "--smbios1",
            "uuid=" + spec["uuid"],
            "--memory",
            str(spec["memoryMiB"]),
            "--cores",
            str(spec["vcpus"]),
            "--bios",
            "ovmf",
            "--machine",
            "q35",
            "--ostype",
            "l26",
            "--agent",
            "enabled=1",
            *[
                argument
                for i, adapter in enumerate(common.network_adapters(spec, BACKEND))
                for argument in (
                    f"--net{i}",
                    "virtio=" + adapter["mac"] + ",bridge=" + adapter["bridge"],
                )
            ],
            "--efidisk0",
            spec["storage"] + ":0,efitype=4m,pre-enrolled-keys=0",
            "--virtio0",
            (
                spec["storage"] + ":" + str(spec["diskGiB"])
                if installing
                else spec["storage"] + ":0,import-from=" + str(image)
            ),
            *(["--ide2", iso_volume + ",media=cdrom"] if installing else []),
            "--boot",
            "order=ide2;virtio0" if installing else "order=virtio0",
            "--serial0",
            "socket",
            "--vga",
            "serial0",
        ],
        timeout=1800,
    )
    config = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                f"/nodes/{spec['node']}/qemu/{spec['vmid']}/config",
                "--output-format",
                "json",
            ]
        )
    )
    volume = config["virtio0"].split(",")[0]
    if not volume.startswith(spec["storage"] + ":"):
        raise RuntimeError(_("Proxmox created a disk outside the declared storage."))
    hardware = {
        "diskVolume": volume,
        "efiVolume": config["efidisk0"].split(",")[0],
        **({"isoVolume": iso_volume} if installing else {}),
    }
    with (directory / "hardware.json").open("x", encoding="utf-8") as stream:
        json.dump(hardware, stream)


def inventory(spec, directory, declared):
    machines, images, errors, references = [], [], [], {}
    prefix = f"/nodes/{spec['node']}"
    for vm in registry():
        if vm.get("type") != "qemu" or vm.get("node") != spec["node"]:
            continue
        row = {
            "name": vm.get("name"),
            "vmid": vm["vmid"],
            "state": vm["status"],
            "declared": any(s.get("vmid") == vm["vmid"] for s in declared),
            "capacityBytes": vm.get("maxdisk"),
            "allocatedBytes": None,
            "classification": "unknown",
        }
        machines.append(row)
        try:
            base = prefix + "/qemu/" + str(vm["vmid"])
            snapshots = json.loads(
                common.run(
                    ["pvesh", "get", base + "/snapshot", "--output-format", "json"]
                )
            )
            paths = [(base + "/config", "VM:" + str(vm["vmid"]))]
            for snap in snapshots:
                if snap["name"] != "current":
                    paths.append(
                        (
                            base + "/snapshot/" + snap["name"] + "/config",
                            "snapshot:" + str(vm["vmid"]) + ":" + snap["name"],
                        )
                    )
            for path, reason in paths:
                config = json.loads(
                    common.run(["pvesh", "get", path, "--output-format", "json"])
                )
                for key, value in config.items():
                    if re.fullmatch(
                        r"(?:virtio|scsi|sata|ide|efidisk|tpmstate|unused)\d+", key
                    ) and isinstance(value, str):
                        volume = value.split(",")[0]
                        references.setdefault(volume, []).append(reason)
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            errors.append(str(vm["vmid"]) + ": " + str(error))
        row["classification"] = (
            "protected" if row["declared"] or row["state"] == "running" else "review"
        )
    volumes = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                prefix + f"/storage/{spec['storage']}/content",
                "--content",
                "images",
                "--output-format",
                "json",
            ]
        )
    )
    for volume in volumes:
        row = {
            "path": volume["volid"],
            "capacityBytes": volume.get("size"),
            "allocatedBytes": volume.get("used"),
            "protectedBy": references.get(volume["volid"], []),
            "classification": "unknown",
        }
        try:
            path = Path(common.run(["pvesm", "path", volume["volid"]]))
            if path.is_file():
                row.update(common.file_info(path))
                row["volume"] = volume["volid"]
        except (OSError, RuntimeError) as error:
            errors.append(volume["volid"] + ": " + str(error))
        if row["protectedBy"]:
            row["classification"] = "protected"
        images.append(row)
    for machine in machines:

        def belongs(reason):
            return reason == "VM:" + str(machine["vmid"]) or reason.startswith(
                "snapshot:" + str(machine["vmid"]) + ":"
            )

        needed = {
            volume
            for volume, reasons in references.items()
            if any(belongs(reason) for reason in reasons)
        }
        attached = [
            image
            for image in images
            if any(belongs(reason) for reason in image["protectedBy"])
        ]
        if (
            attached
            and len(attached) == len(needed)
            and all(image["allocatedBytes"] is not None for image in attached)
        ):
            machine["allocatedBytes"] = sum(
                image["allocatedBytes"] for image in attached
            )
    return {
        "machines": machines,
        "images": images,
        "errors": errors,
        "note": _(
            "Proxmox: node VMs and snapshots, images in the selected storage. Block/thin storage may not report physical allocation; null is not zero. Storage shared with other nodes prevents proof that orphaned volumes are unused."
        ),
    }


def installer_media(spec, directory, marker, detach):
    info = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                f"/nodes/{spec['node']}/qemu/{spec['vmid']}/config",
                "--output-format",
                "json",
            ]
        )
    )
    volume, iso_path = iso_medium(spec)
    hardware = json.loads((directory / "hardware.json").read_text())
    if (
        info.get("ide2", "").split(",")[0] != volume
        or hardware.get("isoVolume") != volume
    ):
        raise RuntimeError(_("The VM is not using the expected ISO."))
    if not iso_path.is_file():
        raise RuntimeError(_("The managed installation ISO is missing."))
    with iso_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != marker["sha256"]:
            raise RuntimeError(_("Managed ISO checksum does not match."))
    if detach:
        common.run(
            [
                "qm",
                "set",
                str(spec["vmid"]),
                "--delete",
                "ide2",
                "--boot",
                "order=virtio0",
            ]
        )
        iso_path.unlink()


def reset_checks(spec, directory, marker, snapshot, result):
    base = "/nodes/" + spec["node"] + "/qemu/" + str(spec["vmid"])
    config = json.loads(
        common.run(["pvesh", "get", base + "/config", "--output-format", "json"])
    )
    if config.get("lock") or config.get("protection") or config.get("template"):
        raise RuntimeError(
            _("The Proxmox VM is locked, protected or a template; refusing reset.")
        )
    snaps = json.loads(
        common.run(["pvesh", "get", base + "/snapshot", "--output-format", "json"])
    )
    if any(s["name"] != "current" for s in snaps):
        raise RuntimeError(_("The VM has snapshots; refusing reset."))
    hardware = json.loads((directory / "hardware.json").read_text(encoding="utf-8"))
    volumes = []
    for key in ("virtio0", "efidisk0"):
        volume = config[key].split(",")[0]
        storage, name = volume.split(":", 1)
        expected = hardware.get(
            "diskVolume" if key == "virtio0" else "efiVolume", volume
        )
        if (
            volume != expected
            or storage != spec["storage"]
            or not re.fullmatch(
                r"(?:"
                + str(spec["vmid"])
                + r"/)?vm-"
                + str(spec["vmid"])
                + r"-disk-\d+(?:\.(?:raw|qcow2|vmdk))?",
                name,
            )
        ):
            raise RuntimeError(
                _(
                    "The Proxmox VM disk is not a verified volume of this instance; refusing reset."
                )
            )
        volumes.append(volume)
    media = []
    for key, value in config.items():
        if not re.fullmatch(
            r"(?:ide|sata|scsi|virtio|efidisk|tpmstate|unused)\d+", key
        ) or key in ("virtio0", "efidisk0"):
            continue
        if key != "ide2" or value.split(",")[0] not in (
            "none",
            hardware.get("isoVolume"),
        ):
            raise RuntimeError(
                _(
                    "The Proxmox VM has an additional disk or foreign medium; refusing reset."
                )
            )
    if marker.get("method") == "iso":
        volume, path = iso_medium(spec)
        if path.exists():
            if path.is_symlink() or hardware.get("isoVolume") != volume:
                raise RuntimeError(
                    _("The managed ISO is not verified; refusing reset.")
                )
            with path.open("rb") as stream:
                if (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    != marker["sha256"]
                ):
                    raise RuntimeError(
                        _("The managed ISO has different content; refusing reset.")
                    )
            media.append(str(path))
            volumes.append(volume)
    for peer in registry():
        if peer.get("type") not in ("qemu", "lxc") or (
            peer.get("node") == spec["node"]
            and peer.get("vmid") == spec["vmid"]
            and peer.get("type") == "qemu"
        ):
            continue
        peer_base = (
            "/nodes/" + peer["node"] + "/" + peer["type"] + "/" + str(peer["vmid"])
        )
        paths = [peer_base + "/config"]
        snapshots = json.loads(
            common.run(
                ["pvesh", "get", peer_base + "/snapshot", "--output-format", "json"]
            )
        )
        for snap in snapshots:
            if snap["name"] != "current":
                if not re.fullmatch(r"[A-Za-z0-9_-]+", snap["name"]):
                    raise RuntimeError(
                        _("Cannot safely verify another VM's snapshot; refusing reset.")
                    )
                paths.append(peer_base + "/snapshot/" + snap["name"] + "/config")
        for path in paths:
            info = json.loads(
                common.run(["pvesh", "get", path, "--output-format", "json"])
            )
            if any(
                v in re.split(r"[,;=\s]+", str(value))
                for v in volumes
                for value in info.values()
            ):
                raise RuntimeError(
                    _(
                        "Another VM, container or snapshot uses the volume or ISO; refusing reset."
                    )
                )
    result.update(volumes=volumes, media=media)

    return result


def shutdown(spec, directory):
    common.run(
        ["qm", "shutdown", str(spec["vmid"]), "--timeout", "120", "--forceStop", "0"],
        timeout=150,
    )


def remove(spec, directory, final):
    common.run(["qm", "destroy", str(spec["vmid"]), "--purge", "1"], timeout=180)
    contents = json.loads(
        common.run(
            [
                "pvesh",
                "get",
                "/nodes/" + spec["node"] + "/storage/" + spec["storage"] + "/content",
                "--output-format",
                "json",
            ]
        )
    )
    if any(item.get("volid") in final["volumes"] for item in contents):
        raise RuntimeError(
            _(
                "The VM was removed, but its volume remains; keeping records for manual inspection."
            )
        )
    for media in final["media"]:
        Path(media).unlink()


def start(spec, directory):
    common.run(["qm", "start", str(spec["vmid"])])


def check_iso(spec, image_bytes):
    _volume, iso_path = iso_medium(spec)
    if iso_path.exists():
        raise RuntimeError(
            _("The target's managed ISO already exists; nothing will be overwritten.")
        )
    if shutil.disk_usage(iso_path.parent).free < image_bytes:
        raise RuntimeError(
            _("There is not enough free space in the Proxmox ISO storage.")
        )
