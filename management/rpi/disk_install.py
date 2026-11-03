#!/usr/bin/env python3
"""Prepare a declared RPi SSD on a Linux workstation without contacting the RPi."""

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys

from messages import _, ArgumentParser, error, info, success, warning


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def descendants(node):
    yield node
    for child in node.get("children", []):
        yield from descendants(child)


def validate_local_path(value):
    require(re.fullmatch(r"/dev/disk/by-id/[A-Za-z0-9_:.+\-]+", value) is not None
            and not re.search(r"-part\d+$", value),
            _("Select a whole SSD using its local /dev/disk/by-id/ path."))
    return value


def disk_identity(snapshot, disk):
    return [disk["path"], disk["maj:min"], disk["size"], disk["serial"], snapshot["diskseq"]]


def partition_paths(device):
    prefix = device + ("p" if device[-1].isdigit() else "")
    return [prefix + "1", prefix + "2"]


def validate_disk(snapshot, target, *, erase_existing=False):
    disks = snapshot["disks"]
    require(all(disk["type"] != "part" for disk in disks),
            _("The disk listing is not a tree; partitions cannot be assigned safely."))
    matches = [disk for disk in disks if disk["path"] == snapshot["device"]]
    require(len(matches) == 1 and matches[0]["type"] == "disk",
            _("The target is not an unambiguous physical disk."))
    disk = matches[0]
    require(target.get("diskSerial") and (disk.get("serial") or "").strip() == target["diskSerial"],
            _("Disk serial number does not match."))
    require(disk["size"] >= target["minimumDiskBytes"], _("The target disk is too small."))
    require(not any(node.get("ro") for node in descendants(disk)), _("The target disk is read-only."))
    require(not snapshot["holders"] and not snapshot["busy"]
            and not any(point for node in descendants(disk) for point in node.get("mountpoints", [])),
            _("The target disk is mounted, used as swap, or held by another device or mount namespace."))
    require(all(node["type"] == "part" for child in disk.get("children", []) for node in descendants(child)),
            _("The disk has child devices other than regular partitions."))
    require(erase_existing or (not disk.get("children") and not snapshot["signatures"]),
            _("The target disk contains data; review the plan with --erase-existing."))
    for other in disks:
        if other["path"] != disk["path"]:
            require(not any(node.get("partlabel") in (target["bootLabel"], target["rootLabel"])
                            for node in descendants(other)), _("Another disk uses the target partition label."))
    return disk


def output(command):
    return subprocess.check_output(command, text=True)


def probe(device):
    resolved = str(Path(device).resolve(strict=True))
    require(stat.S_ISBLK(os.stat(resolved).st_mode), _("The target is not a block device."))
    disks = json.loads(output(["lsblk", "--tree", "--json", "--bytes", "--output",
                              "PATH,TYPE,SIZE,SERIAL,PARTLABEL,MOUNTPOINTS,RO,MAJ:MIN"]))["blockdevices"]
    matches = [disk for disk in disks if disk["path"] == resolved]
    nodes = list(descendants(matches[0])) if len(matches) == 1 else []
    numbers = {node["maj:min"] for node in nodes}
    holders = [str(holder) for number in numbers
               for holder in Path("/sys/dev/block", number, "holders").glob("*")]
    busy = []
    # lsblk reports this namespace. Root additionally checks every visible process
    # namespace, so a disk mounted only by a container is still protected.
    for mountinfo in Path("/proc").glob("[0-9]*/mountinfo"):
        try:
            busy.extend(line.split()[2] for line in mountinfo.read_text().splitlines()
                        if line.split()[2] in numbers)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            require(os.geteuid() != 0, _("Cannot inspect all mount namespaces safely."))
    signatures = json.loads(output(["wipefs", "--no-act", "--json", resolved])).get("signatures", [])
    require(len(matches) == 1, _("The target is not an unambiguous physical disk."))
    sequence = int(Path("/sys/dev/block", matches[0]["maj:min"], "diskseq").read_text())
    return {"device": resolved, "diskseq": sequence, "disks": disks, "holders": holders,
            "busy": busy, "signatures": signatures}


def bind(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(["mount", "--bind", str(source), str(destination)], check=True)
    subprocess.run(["mount", "-o", "remount,bind,ro", str(destination)], check=True)


def emulation_mounts(root):
    bind("/nix/store", root / "nix/store")
    # Activation remounts /run. Pre-mount it so the interpreter bind survives;
    # otherwise specialfs would cover /run/binfmt halfway through activation.
    (root / "run").mkdir(parents=True, exist_ok=True)
    subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", str(root / "run")], check=True)
    if Path("/run/binfmt").is_dir():
        bind("/run/binfmt", root / "run/binfmt")


def install(target, device, *, erase_existing, expected):
    """Runs only as root in a new private mount namespace; never activates services."""
    require(os.stat("/proc/self/ns/mnt").st_ino != os.stat("/proc/1/ns/mnt").st_ino,
            _("The local installation worker requires a private mount namespace."))
    snapshot = probe(device)
    disk = validate_disk(snapshot, target, erase_existing=erase_existing)
    require(disk_identity(snapshot, disk) == expected,
            _("The local disk identity changed after planning; the disk was not changed."))
    require(not Path("/mnt").is_symlink(),
            _("Local installation requires a real /mnt directory."))
    Path("/mnt").mkdir(mode=0o755, exist_ok=True)
    require(subprocess.run(["mountpoint", "--quiet", "/mnt"]).returncode == 32,
            _("/mnt is already mounted."))
    # All mounts, the disko alias and the chroot interpreter disappear with this
    # process's private namespace. Never create a device alias in the host /dev.
    subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", "/mnt"], check=True)
    scratch = Path("/mnt")
    (scratch / "disk").symlink_to(disk["path"])
    for number, partition in enumerate(partition_paths(disk["path"]), 1):
        (scratch / ("part" + str(number))).symlink_to(partition)
    preflight = scratch / "emulation-check"
    emulation_mounts(preflight)
    try:
        subprocess.run(["chroot", str(preflight), target["systemPath"] + "/sw/bin/true"], check=True)
    except subprocess.SubprocessError:
        raise RuntimeError(_("ARM execution in the installation chroot is unavailable; enable aarch64-linux binfmt before erasing the SSD.")) from None
    subprocess.run(["umount", "--recursive", str(preflight / "run")], check=True)
    subprocess.run(["umount", str(preflight / "nix/store")], check=True)
    # Serialize NixBerry installers without locking the block node: udev needs
    # to probe it while disko creates and rereads the partition table.
    lock = Path("/run/lock") / ("nixberry-local-" + disk["maj:min"].replace(":", "-") + ".lock")
    lock.parent.mkdir(mode=0o755, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    disk_descriptor = None
    changed = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep the kernel disk object open throughout installation. Unlike an
        # exclusive block lock, this allows partition rereads and udev probing.
        disk_descriptor = os.open(disk["path"], os.O_RDONLY | os.O_NONBLOCK)
        sequence = struct.unpack("Q", fcntl.ioctl(disk_descriptor, 0x80081280, bytes(8)))[0]  # BLKGETDISKSEQ
        current_snapshot = probe(device)
        current = validate_disk(current_snapshot, target, erase_existing=erase_existing)
        require(disk_identity(current_snapshot, current) == expected and sequence == expected[-1]
                and os.fstat(disk_descriptor).st_rdev == os.stat(device).st_rdev,
                _("The local disk identity changed after planning; the disk was not changed."))
        # The exclusive-open check catches a new mount since the last probe.
        # Close it before partitioning so BLKRRPART and udev can access the disk.
        check = os.open(current["path"], os.O_RDONLY | os.O_EXCL)
        os.close(check)
        if erase_existing:
            changed = True
            for node in reversed(list(descendants(current))):
                subprocess.run(["wipefs", "--all", node["path"]], check=True)
            subprocess.run(["udevadm", "settle"], check=True)
        root = scratch / "root"
        try:
            changed = True
            subprocess.run([target["localDiskoPath"]], check=True)
            subprocess.run(["nixos-install", "--root", str(root), "--system", target["systemPath"],
                            "--no-bootloader", "--no-root-passwd", "--no-channel-copy",
                            "--option", "substituters", "", "--option", "extra-substituters", ""], check=True)
            # Native nixos-enter runs the target activation/boot scripts in its
            # own chroot; host store/interpreter mounts are temporary and read-only.
            emulation_mounts(root)
            subprocess.run(["nixos-enter", "--root", str(root), "--",
                            target["systemPath"] + "/bin/switch-to-configuration", "boot"], check=True)
            generations = root / "boot/rpi-generations"
            require(generations.is_dir() and (root / "boot/config.txt").is_file(),
                    _("Generated RPi boot files are missing."))
            require((root / "nix/var/nix/profiles/system").readlink().name.startswith("system-"),
                    _("The installed system profile is missing."))
            subprocess.run(["sync", "-f", str(root)], check=True)
        finally:
            if root.is_dir() and subprocess.run(["mountpoint", "--quiet", str(root)]).returncode == 0:
                subprocess.run(["umount", "--recursive", str(root)], check=True)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        if changed:
            warning(_("SSD installation is incomplete. Do not boot it; fix the error and repeat the reviewed plan with --erase-existing."))
        raise
    finally:
        if disk_descriptor is not None:
            os.close(disk_descriptor)
        os.close(descriptor)


def main(argv=None):
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument("--local-disk", required=True)
    parser.add_argument("--manifest", default=os.environ.get("NIXBERRY_RPI_DISK_MANIFEST"))
    parser.add_argument("--erase-existing", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected", help=__import__("argparse").SUPPRESS)
    args = parser.parse_args(argv)
    require(sys.platform == "linux", _("Direct RPi SSD installation requires a Linux workstation."))
    device = validate_local_path(args.local_disk)
    require(args.manifest, _("The local SSD target manifest is missing."))
    target = json.loads(Path(args.manifest).read_text())
    require(target.get("security", {}).get("mode", "none") == "none",
            _("Direct SSD installation currently supports unencrypted RPi targets only; use recovery media for LUKS."))
    if os.geteuid() != 0:
        command = ["sudo", "--", "env", "PATH=" + os.environ["PATH"],
                   "PYTHONPATH=" + os.environ.get("PYTHONPATH", ""),
                   "NIXBERRY_LANG=" + os.environ.get("NIXBERRY_LANG", "en"),
                   sys.executable, str(Path(__file__).resolve()), "--manifest", args.manifest,
                   "--local-disk", device, "--apply" if args.apply else "--dry-run"]
        if args.erase_existing:
            command.append("--erase-existing")
        return subprocess.run(command, check=False).returncode
    snapshot = probe(device)
    disk = validate_disk(snapshot, target, erase_existing=args.erase_existing)
    expected = disk_identity(snapshot, disk)
    info(_("Local SSD: {device}; serial: {serial}; capacity: {capacity} bytes."),
         device=device, serial=disk["serial"], capacity=disk["size"])
    if not args.apply:
        info(_("Local SSD installation plan verified; nothing was changed. No RPi connection was made."))
        if args.erase_existing:
            warning(_("The plan includes erasing existing partitions; this only happens with --apply."))
        return 0
    if args.expected:
        require(os.geteuid() == 0, _("The local installation worker requires root."))
        install(target, device, erase_existing=args.erase_existing, expected=json.loads(args.expected))
        success(_("SSD installation completed and unmounted. Connect it to the declared RPi and verify boot; application data restoration is separate."))
        return 0
    require(shutil.which("unshare"), _("Local SSD installation requires util-linux unshare."))
    command = ["env", "PATH=" + os.environ["PATH"],
               "PYTHONPATH=" + os.environ.get("PYTHONPATH", ""),
               "NIXBERRY_LANG=" + os.environ.get("NIXBERRY_LANG", "en"),
               "unshare", "--mount", "--propagation", "private", "--", sys.executable,
               str(Path(__file__).resolve()), "--manifest", args.manifest,
               "--local-disk", device, "--apply", "--expected", json.dumps(expected)]
    if args.erase_existing:
        command.append("--erase-existing")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
