#!/usr/bin/env python3
"Remove only the dedicated test VM and its disk; the default mode changes nothing."

from messages import _, ArgumentParser, info, error as report_error

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
import policy
from storage import reject_shared, reject_shared_domains


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_target(domain_xml, volume_xml, manifest):
    root = ET.fromstring(domain_xml)
    policy.owner(root, manifest)
    volume = ET.fromstring(volume_xml)
    require(root.findtext("name") == manifest["name"], _("VM name does not match."))
    require(root.findtext("uuid") == manifest["uuid"], _("VM UUID does not match."))
    disks = root.findall("./devices/disk[@device='disk']")
    require(len(disks) == 1, _("The VM does not have exactly one disk."))
    source = disks[0].find("source")
    require(source is not None and source.get("file") == manifest["diskPath"], _("Disk path does not match."))
    require(disks[0].find("target").get("dev") == "vda", _("The target disk is not vda."))
    require(volume.findtext("name") == manifest["volume"], _("Volume name does not match."))
    require(volume.findtext("target/path") == manifest["diskPath"], _("Volume path does not match."))
    require(volume.findtext("capacity") == str(manifest["diskGiB"] * 1024**3), _("Volume size does not match."))
    require(volume.find("target/format").get("type") == "qcow2", _("The volume is not qcow2."))
    require(volume.find("backingStore/path") is None, _("The volume uses a backing image."))
    nvram = root.findtext("./os/nvram")
    require(nvram == "/var/lib/libvirt/qemu/nvram/" + manifest["name"] + "_VARS.fd",
            _("Test VM NVRAM path does not match."))
    return manifest["diskPath"], nvram


def main():
    parser = ArgumentParser(description=_("Remove only the dedicated test VM and its disk; the default mode changes nothing."))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(Path(os.environ["NIXBERRY_MANIFEST"]).read_text())
    policy.validate(manifest)
    require(manifest.get("allowReset", False) is True, _("Removal of this instance is not declaratively allowed."))
    require(manifest.get("ownerId") == "nixberry-" + manifest["uuid"],
            _("Reset requires a declared NixBerry owner; a known UUID alone is not enough."))
    require(manifest["volume"] == manifest["name"] + ".qcow2",
            _("The volume does not have the dedicated test VM name."))

    def virsh(*arguments):
        return subprocess.check_output(
            ["virsh", "--connect", manifest["uri"], *arguments], text=True, timeout=20,
            env={**os.environ, "LC_ALL": "C"}
        ).strip()

    def validate():
        volume = virsh("vol-dumpxml", manifest["volume"], "--pool", manifest["storagePool"])
        disk, nvram = validate_target(virsh("dumpxml", manifest["name"], "--inactive"), volume, manifest)
        for filename in (disk, nvram):
            path = Path(filename)
            require(path.is_file() and not path.is_symlink() and not path.is_mount()
                    and path.stat().st_nlink == 1 and not any(p.is_symlink() for p in path.parents),
                    _("The disk or NVRAM is missing, is a link or is a shared file; refusing reset."))
        image = json.loads(subprocess.check_output(
            ["qemu-img", "info", "-U", "--output=json", disk], text=True, timeout=20))
        require(image.get("format") == "qcow2" and image.get("virtual-size") == manifest["diskGiB"] * 1024**3
                and not image.get("backing-filename") and not image.get("full-backing-filename")
                and not image.get("snapshots"),
                _("The actual image has a different format, capacity, backing image or internal snapshot; refusing reset."))
        state = virsh("domstate", manifest["name"])
        require(state in ("running", "shut off"), _("The VM is not in a supported state."))
        if state == "running":
            validate_target(virsh("dumpxml", manifest["name"]), volume, manifest)
        require(not virsh("snapshot-list", manifest["name"], "--name"),
                _("The VM has snapshots; refusing removal."))
        reject_shared_domains(virsh, manifest["name"], disk, nvram)
        return state, disk, nvram

    state, disk, nvram = validate()
    info(_("VM: {value1}; UUID: {value2}; state: {state}"), value1=manifest['name'], value2=manifest['uuid'], state=state)
    info(_("Remove only: {disk} and {nvram}"), disk=disk, nvram=nvram)
    if not args.apply:
        info(_("Removal plan verified. Nothing was changed."))
        return
    if state == "running":
        virsh("shutdown", manifest["name"])
        deadline = time.monotonic() + 120
        while virsh("domstate", manifest["name"]) != "shut off":
            require(time.monotonic() < deadline, _("The VM did not shut down; nothing will be deleted."))
            time.sleep(2)
    state, unused, unused = validate()
    require(state == "shut off", _("The VM is not powered off; nothing will be deleted."))
    virsh("undefine", manifest["name"], "--nvram")
    virsh("vol-delete", manifest["volume"], "--pool", manifest["storagePool"])
    require(not os.path.lexists(disk), _("The disk file still exists after removal."))
    require(not os.path.lexists(nvram), _("NVRAM still exists after removal."))
    info(_("Only the dedicated test VM, its disk and NVRAM were removed."))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError, ET.ParseError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
