#!/usr/bin/env python3
"Install only into the dedicated VM; without --apply, only check the plan."

from messages import _, ArgumentParser, info, success, shell_script, forward_stderr, error as report_error

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import policy
from storage import reject_shared_domains


def remember_host_key(source, manifest):
    """Retain the key only after verifying VM identity and the installed system."""
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    destination = root / "nixberry/portable" / manifest["uuid"] / "known_hosts"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(Path(source).read_bytes())
    temporary.chmod(0o600)
    temporary.replace(destination)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_domain(xml, manifest):
    root = ET.fromstring(xml)
    policy.owner(root, manifest)
    require(root.findtext("name") == manifest["name"], _("VM name does not match."))
    require(root.findtext("uuid") == manifest["uuid"], _("VM UUID does not match."))
    disks = root.findall("./devices/disk[@device='disk']")
    require(len(disks) == 1, _("The VM must have exactly one target disk."))
    require(disks[0].find("source").get("file") == manifest["diskPath"], _("Disk path does not match."))
    require(disks[0].find("target").get("dev") == "vda", _("The target disk is not vda."))
    cds = root.findall("./devices/disk[@device='cdrom']")
    if manifest.get("deploymentMethod", "iso") == "image":
        require(all(cd.find("source") is None or not cd.find("source").attrib for cd in cds),
                _("The image method must not have an installation ISO attached."))
    else:
        require(len(cds) == 1 and cds[0].find("target").get("dev") == "sda", _("CD-ROM does not match."))
        require(cds[0].find("source") is not None and cds[0].find("source").get("file") == manifest["isoPath"],
                _("The VM is not using the expected ISO."))
    interfaces = root.findall("./devices/interface")
    require(len(interfaces) == 1, _("The VM must have exactly one network adapter."))
    require(interfaces[0].find("mac").get("address") == manifest["mac"], _("MAC address does not match."))
    require(interfaces[0].find("source").get("network") == manifest["network"], _("Network does not match."))


def choose_address(output, network_xml, mac):
    root = ET.fromstring(network_xml)
    require(root.find("forward").get("mode") == "nat", _("The network has no NAT."))
    address = root.find("ip")
    network = ipaddress.ip_network(address.get("address") + "/" + address.get("netmask"), strict=False)
    candidates = []
    for line in output.splitlines():
        columns = line.split()
        if len(columns) >= 4 and columns[1].lower() == mac and columns[2] == "ipv4":
            candidate = ipaddress.ip_interface(columns[3]).ip
            require(candidate in network and str(candidate) != address.get("address"),
                    _("The guest address is outside the expected network."))
            candidates.append(str(candidate))
    require(len(set(candidates)) == 1, _("Cannot unambiguously determine the VM address."))
    return candidates[0]


def installed_check(manifest):
    return (
        "set -eu; "
        "test \"$(cat /sys/class/dmi/id/product_uuid)\" = " + shlex.quote(manifest["uuid"]) + "; "
        "test \"$(hostname)\" = " + shlex.quote(manifest.get("guestHostName", "nixberry-vm")) + "; "
        "test \"$(readlink -f /run/current-system)\" = " + shlex.quote(manifest["systemPath"]) + "; "
        "test \"$(findmnt -n -o FSTYPE /)\" = ext4; "
        "test -d /sys/firmware/efi; "
        "! mountpoint -q /iso; "
        "for service in sshd systemd-networkd qemu-guest-agent; do "
        'systemctl is-active --quiet "$service" || exit 1; done; '
        "hostname; findmnt -n -o SOURCE,FSTYPE /; readlink -f /run/current-system"
    )


def image_install(manifest, apply):
    def virsh(*arguments):
        return subprocess.check_output(["virsh", "--connect", manifest["uri"], *arguments], text=True, timeout=20).strip()
    validate_domain(virsh("dumpxml", manifest["name"], "--inactive"), manifest)
    reject_shared_domains(virsh, manifest["name"], manifest["diskPath"])
    state = virsh("domstate", manifest["name"])
    require(state in ("running", "shut off"), _("The VM is not ready to boot from disk."))
    if not apply:
        info(_("Image method: install starts and verifies the prebuilt system; the disk is not formatted."))
        return
    if state == "shut off":
        virsh("start", manifest["name"])
    with tempfile.TemporaryDirectory(prefix="nixberry-image-ssh-") as directory:
        deadline, failure = time.monotonic() + 240, None
        while time.monotonic() < deadline:
            try:
                validate_domain(virsh("dumpxml", manifest["name"]), manifest)
                address = choose_address(virsh("domifaddr", manifest["name"], "--source", "lease"),
                                         virsh("net-dumpxml", manifest["network"]), manifest["mac"])
                result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                    "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + directory + "/known_hosts",
                    "root@" + address, installed_check(manifest)], text=True, capture_output=True, timeout=30)
                if result.returncode == 0:
                    forward_stderr(result.stderr)
                    remember_host_key(Path(directory) / "known_hosts", manifest)
                    print(result.stdout, end="", flush=True)
                    success(_("Boot of the prebuilt system from disk verified."))
                    return
                failure = result.stderr
            except (RuntimeError, subprocess.SubprocessError) as error:
                failure = str(error)
            time.sleep(3)
        raise RuntimeError(_("The VM was started, but system verification failed: {error}").format(error=failure))


def main():
    parser = ArgumentParser(description=_("Install only into the dedicated VM; without --apply, only check the plan."))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(Path(os.environ["NIXBERRY_MANIFEST"]).read_text())
    policy.validate(manifest)
    if manifest.get("deploymentMethod", "iso") == "image":
        return image_install(manifest, args.apply)

    def virsh(*arguments):
        return subprocess.check_output(
            ["virsh", "--connect", manifest["uri"], *arguments], text=True, timeout=20
        ).strip()

    def validate():
        require(virsh("domstate", manifest["name"]) == "running", _("The VM is not running."))
        for extra in ((), ("--inactive",)):
            validate_domain(virsh("dumpxml", manifest["name"], *extra), manifest)
        reject_shared_domains(virsh, manifest["name"], manifest["diskPath"])

    validate()
    address = choose_address(
        virsh("domifaddr", manifest["name"], "--source", "lease"),
        virsh("net-dumpxml", manifest["network"]), manifest["mac"]
    )
    info(_('VM: {value1}; UUID: {value2}; IP: {address}'), value1=manifest['name'], value2=manifest['uuid'], address=address)
    info(_("Disk: {value1}; system: {value2}"), value1=manifest['diskPath'], value2=manifest['systemPath'])

    with tempfile.TemporaryDirectory(prefix="nixberry-install-ssh-") as state:
        def ssh(command, phase, *, input_text=None, capture=False):
            return subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                 "-o", "StrictHostKeyChecking=accept-new",
                 "-o", f"UserKnownHostsFile={state}/{phase}.known_hosts",
                 f"root@{address}", command],
                input=input_text, text=True, capture_output=capture, check=False
            )

        guest_args = ["bash", "-s", "--", "dry-run",
                      manifest["uuid"], manifest["systemPath"], manifest["diskoPath"],
                      str(manifest["diskGiB"] * 1024**3)]
        result = ssh(shlex.join(guest_args), "installer",
                     input_text=shell_script(os.environ["NIXBERRY_GUEST_INSTALL"]))
        require(result.returncode == 0, _("Guest check or installation failed; leaving the VM boot configuration unchanged."))
        if not args.apply:
            info(_("Plan verified. The disk was not changed and the VM was not shut down."))
            return

        # Repeat host checks after guest checks, immediately before writing.
        validate()
        guest_args[3] = "apply"
        result = ssh(shlex.join(guest_args), "installer",
                     input_text=shell_script(os.environ["NIXBERRY_GUEST_INSTALL"]))
        require(result.returncode == 0, _("Guest installation failed; leaving the VM boot configuration unchanged."))

        success(_("Installation completed; gracefully shutting down the test VM."))
        virsh("shutdown", manifest["name"])
        deadline = time.monotonic() + 120
        while virsh("domstate", manifest["name"]) != "shut off":
            require(time.monotonic() < deadline, _("The VM did not shut down; the ISO will not be detached."))
            time.sleep(2)
        virsh("change-media", manifest["name"], "sda", "--eject", "--config")
        virsh("start", manifest["name"])
        info(_("ISO detached; waiting for SSH on the installed system."))
        verify = installed_check(manifest)
        deadline = time.monotonic() + 120
        while True:
            result = ssh(verify, "installed", capture=True)
            if result.returncode == 0:
                remember_host_key(Path(state) / "installed.known_hosts", manifest)
                print(result.stdout, end="", flush=True)
                break
            require(time.monotonic() < deadline, _("Installed system check failed: {value1}").format(value1=result.stderr))
            time.sleep(2)
        success(_("Verified: boot from ext4 disk without ISO, UEFI, exact system and SSH."))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
