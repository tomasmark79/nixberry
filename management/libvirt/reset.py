#!/usr/bin/env python3
"Remove only the dedicated test VM and its disk; the default mode changes nothing."

from messages import _, ArgumentParser, info, warning, error as report_error

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import policy
from storage import reject_shared, reject_shared_domains


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parent_links(path):
    return tuple((str(component), os.readlink(component))
                 for component in reversed(path.parents) if component.is_symlink())


def validate_file(filename):
    path = Path(filename)
    # Directory aliases are allowed, but leaf links and shared files are not.
    require(not path.is_symlink(),
            _("Reset path contains a symbolic link: {path}; refusing reset.").format(path=path))
    try:
        links = parent_links(path)
        resolved = path.resolve(strict=True)
        data = resolved.lstat()
    except OSError as failure:
        raise RuntimeError(_("Cannot inspect reset path {path}: {error}").format(
            path=path, error=failure)) from failure
    require(stat.S_ISREG(data.st_mode),
            _("Reset path is not a regular file: {path}; refusing reset.").format(path=path))
    require(not resolved.is_mount(),
            _("Reset file is a mount point: {path}; refusing reset.").format(path=path))
    require(data.st_nlink == 1,
            _("Reset file has multiple hard links: {path}; refusing reset.").format(path=path))
    require(not path.is_symlink() and path.resolve(strict=True) == resolved
            and os.path.samestat(data, path.stat()) and parent_links(path) == links,
            _("Reset paths changed during verification; refusing further changes."))
    return {"path": str(path), "resolved": str(resolved), "links": links,
            "identity": (data.st_dev, data.st_ino)}


def show_paths(files):
    if not any(file["links"] for file in files):
        return False
    warning(_("Directory symlinks are present in the reset paths."))
    for path, target in sorted({link for file in files for link in file["links"]}):
        info(_("Directory symlink: {path} → {target}"), path=path, target=target)
    for file in files:
        info(_("Resolved reset path: {path} → {resolved}"),
             path=file["path"], resolved=file["resolved"])
    return True


def confirm_paths():
    require(sys.stdin.isatty(),
            _("Reset through directory symlinks requires an interactive terminal; nothing was changed."))
    info(_("Remove this VM and only its verified files through these directory symlinks? [y/N]"))
    try:
        confirmed = sys.stdin.readline().strip().lower() in ("y", "yes")
    except KeyboardInterrupt:
        confirmed = False
    if not confirmed:
        info(_("Removal cancelled; nothing was changed."))
    return confirmed


def unchanged(expected, actual):
    require(actual == expected,
            _("Reset paths changed since verification; refusing further changes. Inspect VM and storage state before retrying."))


def validate_volume(volume_xml, manifest):
    volume = ET.fromstring(volume_xml)
    require(volume.findtext("name") == manifest["volume"], _("Volume name does not match."))
    require(volume.findtext("target/path") == manifest["diskPath"], _("Volume path does not match."))
    require(volume.findtext("capacity") == str(manifest["diskGiB"] * 1024**3), _("Volume size does not match."))
    require(volume.find("target/format").get("type") == "qcow2", _("The volume is not qcow2."))
    require(volume.find("backingStore/path") is None, _("The volume uses a backing image."))


def validate_target(domain_xml, volume_xml, manifest):
    root = ET.fromstring(domain_xml)
    policy.owner(root, manifest)
    require(root.findtext("name") == manifest["name"], _("VM name does not match."))
    require(root.findtext("uuid") == manifest["uuid"], _("VM UUID does not match."))
    disks = root.findall("./devices/disk[@device='disk']")
    require(len(disks) == 1, _("The VM does not have exactly one disk."))
    source = disks[0].find("source")
    require(source is not None and source.get("file") == manifest["diskPath"], _("Disk path does not match."))
    require(disks[0].find("target").get("dev") == "vda", _("The target disk is not vda."))
    validate_volume(volume_xml, manifest)
    nvram = root.findtext("./os/nvram")
    require(nvram == "/var/lib/libvirt/qemu/nvram/" + manifest["name"] + "_VARS.fd",
            _("Test VM NVRAM path does not match."))
    return manifest["diskPath"], nvram


def main():
    parser = ArgumentParser(description=_("Remove only the dedicated test VM and its disk; the default mode changes nothing."))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true",
                       help=_("Remove the verified VM; directory symlinks require interactive confirmation"))
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

    def validate(report_paths=False):
        volume = virsh("vol-dumpxml", manifest["volume"], "--pool", manifest["storagePool"])
        disk, nvram = validate_target(virsh("dumpxml", manifest["name"], "--inactive"), volume, manifest)
        files = [validate_file(filename) for filename in (disk, nvram)]
        if report_paths:
            show_paths(files)
        try:
            image = json.loads(subprocess.check_output(
                ["qemu-img", "info", "-U", "--output=json", files[0]["resolved"]], text=True, timeout=20))
        except subprocess.CalledProcessError as failure:
            raise RuntimeError(_("Cannot verify disk image {path}. Check read access and qemu-img diagnostics; nothing was removed.").format(
                path=files[0]["resolved"])) from failure
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
        reject_shared_domains(virsh, manifest["name"], files[0]["resolved"], files[1]["resolved"])
        unchanged(files, [validate_file(filename) for filename in (disk, nvram)])
        return state, disk, nvram, files

    state, disk, nvram, files = validate(report_paths=True)
    info(_("VM: {value1}; UUID: {value2}; state: {state}"), value1=manifest['name'], value2=manifest['uuid'], state=state)
    info(_("Remove only: {disk} and {nvram}"), disk=disk, nvram=nvram)
    linked = any(file["links"] for file in files)
    if not args.apply:
        info(_("Removal plan verified. Nothing was changed."))
        return
    if linked and not confirm_paths():
        return
    # Recheck after the user's response, before even requesting shutdown.
    state, unused, unused, current = validate()
    unchanged(files, current)
    if state == "running":
        virsh("shutdown", manifest["name"])
        deadline = time.monotonic() + 120
        while virsh("domstate", manifest["name"]) != "shut off":
            require(time.monotonic() < deadline, _("The VM did not shut down; nothing will be deleted."))
            time.sleep(2)
    state, unused, unused, current = validate()
    unchanged(files, current)
    require(state == "shut off", _("The VM is not powered off; nothing will be deleted."))
    virsh("undefine", manifest["name"], "--nvram")
    info(_("The VM definition and NVRAM were removed; verifying the disk before removal."))
    # A redirect or file replacement between the two removal operations must
    # leave the remaining disk untouched. Do not attempt uncertain cleanup.
    validate_volume(virsh("vol-dumpxml", manifest["volume"], "--pool", manifest["storagePool"]), manifest)
    unchanged(files[0], validate_file(disk))
    unchanged((files[1]["resolved"], files[1]["links"]),
              (str(Path(nvram).resolve()), parent_links(Path(nvram))))
    require(not os.path.lexists(files[1]["resolved"]), _("NVRAM still exists after removal."))
    reject_shared_domains(virsh, manifest["name"], files[0]["resolved"], files[1]["resolved"])
    virsh("vol-delete", manifest["volume"], "--pool", manifest["storagePool"])
    require(not os.path.lexists(disk) and not os.path.lexists(files[0]["resolved"]),
            _("The disk file still exists after removal."))
    require(not os.path.lexists(nvram) and not os.path.lexists(files[1]["resolved"]),
            _("NVRAM still exists after removal."))
    info(_("Only the dedicated test VM, its disk and NVRAM were removed."))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError, ET.ParseError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
