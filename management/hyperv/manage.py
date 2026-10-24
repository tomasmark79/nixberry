#!/usr/bin/env python3
"Safely create and install a dedicated Hyper-V VM over Windows SSH."

from messages import _, ArgumentParser, info, success, localized_script, shell_script, forward_stderr, error as report_error

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tempfile
import time
import uuid

from policy import validate


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def choose_address(addresses, host):
    candidates = set()
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and any(address in ipaddress.ip_network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
            require(str(address) not in ("192.168.79.1", host), _("Disallowed guest address."))
            candidates.add(str(address))
    require(len(candidates) <= 1, _("The VM has multiple possible IPv4 addresses."))
    return next(iter(candidates), None)


def installed_check(snapshot, spec):
    encrypted = ""
    if spec.get("security", {}).get("mode", "none") != "none":
        from security import encryption_checks
        encrypted = "; ".join(encryption_checks(spec)) + "\n"
    return "set -eu\n" + encrypted + """
test "$(uname -m)" = x86_64
test "$(cat /sys/class/dmi/id/product_uuid | tr '[:upper:]' '[:lower:]')" = EXPECTED_UUID
test "$(hostname)" = EXPECTED_HOST
test "$(findmnt -n -o SOURCE /)" = EXPECTED_ROOT
test -d /sys/firmware/efi
if mountpoint -q /iso; then exit 1; fi
hostname
readlink -f /run/current-system
lsblk -o PATH,FSTYPE,UUID,MOUNTPOINTS
for service in sshd systemd-networkd hv-kvp hv-vss; do
    systemctl is-active --quiet "$service" || exit 1
done
systemctl --failed --no-legend
test -z "$(systemctl --failed --no-legend)"
""".replace("EXPECTED_UUID", shlex.quote(snapshot["biosGuid"].lower())).replace(
        "EXPECTED_HOST", shlex.quote(spec["guestHostName"])).replace(
        "EXPECTED_ROOT", "/dev/mapper/nixberry-root" if spec.get("security", {}).get("mode", "none") != "none" else "/dev/sda2")


class Manager:
    @property
    def method(self):
        return self.spec.get("deploymentMethod", "image")

    def key_identity(self):
        return self.spec["uuid"] if "uuid" in self.spec else self.spec["ownerId"]

    def remember_host_key(self):
        # Never retain the installer key; store only the verified target system key.
        root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        destination = root / "nixberry/portable" / self.key_identity() / "known_hosts"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes((self.state / "installed-hosts").read_bytes())
        temporary.chmod(0o600)
        temporary.replace(destination)

    def __init__(self, manifest, state):
        validate(manifest)
        self.spec = manifest
        api = runpy.run_path(os.environ["NIXBERRY_HYPERV_MODULE"])
        self.host = api["destination"](manifest)
        self.bootstrap = api["powershell_command"]()
        self.frame_input = api["powershell_input"]
        self.state = Path(state)
        require(self.method in ("image", "iso"), _("Deployment method must be iso or image."))
        media = manifest.get("imagePath" if self.method == "image" else "isoPath")
        require(isinstance(media, str) and bool(media), _("The media path for the selected deployment method is missing."))
        self.media_path = Path(media)
        digest = hashlib.sha256()
        with self.media_path.open("rb") as source:
            while chunk := source.read(4 * 1024**2):
                digest.update(chunk)
        self.sha256 = digest.hexdigest()
        self.media_bytes = self.media_path.stat().st_size

    def remote(self, action, **parameters):
        request = dict(spec=self.spec, action=action, sha256=self.sha256,
                       mediaBytes=self.media_bytes, **parameters)
        encoded = base64.b64encode(json.dumps(request).encode()).decode()
        prefix = "$Request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json\n"
        script = prefix + localized_script(os.environ["NIXBERRY_HYPERV_REMOTE"])
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "--", self.host, self.bootstrap],
            input=self.frame_input(script), text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        require(result.returncode == 0, (result.stderr.strip() or _("The Windows operation failed.")))
        forward_stderr(result.stderr)
        return json.loads(result.stdout.lstrip("\ufeff"))

    def guest(self, address, command, *, installed=False, input_text=None, capture=True, timeout=30):
        known_hosts = self.state / ("installed-hosts" if installed else "installer-hosts")
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + str(known_hosts),
                   "-J", self.host]
        if installed:
            options += ["-o", "HostKeyAlias=nixberry-" + self.key_identity()]
        result = subprocess.run(["ssh", *options, "root@" + address, command],
                                input=input_text, text=True, capture_output=capture, timeout=timeout)
        if capture and result.returncode == 0:
            forward_stderr(result.stderr)
        return result

    def create(self, apply):
        plan = self.remote("plan")
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        if not apply:
            info(_("Plan verified. Method {method}; no media transfer, disk creation or VM creation."), method=self.method)
            return
        token = uuid.uuid4().hex
        upload = self.remote("prepare", token=token)
        if not upload["cached"]:
            destination = self.host + ":" + upload["uploadPath"].replace("\\", "/")
            info(_("Transferring {bytes} media bytes to Windows; SHA256={sha256}"), bytes=self.media_bytes, sha256=self.sha256)
            result = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "--",
                                     str(self.media_path), destination], capture_output=True, text=True, timeout=1200)
            require(result.returncode == 0, (result.stderr or _("Media transfer failed.")))
            forward_stderr(result.stderr)
        snapshot = self.remote("create", token=token)
        if self.method == "iso":
            info(_("VM created and booted from ISO: {value1}"), value1=json.dumps(snapshot))
        else:
            info(_("VM created with a prebuilt disk; it is not running yet."))

    def wait_guest(self, *, installed=False):
        deadline = time.monotonic() + 240
        unlock_attempted = False
        last_message = _("The VM has no IP address yet.")
        while time.monotonic() < deadline:
            snapshot = self.remote("status")
            address = choose_address(snapshot["ips"], self.spec["sshHost"])
            if installed and snapshot["state"] == "Running" and not address and not unlock_attempted:
                from security import unlock_for_install
                unlock_for_install(self.spec, None)
                unlock_attempted = True
            if snapshot["state"] == "Running" and address:
                if installed:
                    command = ("test \"$(readlink -f /run/current-system)\" = " + shlex.quote(self.spec["systemPath"])
                               + " && test \"$(hostname)\" = " + shlex.quote(self.spec["guestHostName"]))
                else:
                    command = "test \"$(hostname)\" = nixberry-installer"
                result = self.guest(address, command, installed=installed)
                if result.returncode == 0:
                    info(_("VM {value1}; IP {address}; SSH available."), value1=snapshot['id'], address=address)
                    return snapshot, address
                last_message = (result.stderr or _("SSH is not available yet.")).strip()[-500:]
                if installed and result.returncode == 255 and not unlock_attempted:
                    from security import unlock_for_install
                    unlock_for_install(self.spec, address)
                    unlock_attempted = True
            time.sleep(4)
        raise RuntimeError(_("Timed out waiting for the guest: {last_message}").format(last_message=last_message))

    def install(self, apply):
        if self.method == "image":
            self.remote("status")
            if not apply:
                info(_("Image method: install starts and verifies the prebuilt system; the disk is not formatted."))
                return
            self.remote("start-disk")
            snapshot, address = self.wait_guest(installed=True)
            result = self.guest(address, installed_check(snapshot, self.spec), installed=True, capture=False)
            require(result.returncode == 0, _("Boot verification from VHDX failed."))
            self.remember_host_key()
            success(_("Boot of the prebuilt system from disk verified."))
            return
        snapshot, address = self.wait_guest()
        require(snapshot["dvd"] == [snapshot["isoPath"]], _("The VM is not using the expected ISO."))
        arguments = ["bash", "-s", "--", "apply" if apply else "dry-run",
                     snapshot["biosGuid"].lower(), self.spec["systemPath"], self.spec["diskoPath"],
                     str(self.spec["diskGiB"] * 1024**3)]
        result = self.guest(address, shlex.join(arguments),
                            input_text=shell_script(os.environ["NIXBERRY_HYPERV_GUEST"]),
                            capture=False, timeout=1800)
        require(result.returncode == 0, _("Guest check or installation failed."))
        if not apply:
            return
        result = self.guest(address, "sync; systemctl poweroff --no-block")
        require(result.returncode in (0, 255), (result.stderr or _("Guest shutdown failed.")))
        deadline = time.monotonic() + 90
        while self.remote("status")["state"] != "Off":
            require(time.monotonic() < deadline, _("The VM did not shut down gracefully."))
            time.sleep(3)
        info(_("VM powered off. Detaching the ISO and configuring boot from VHDX."))
        self.remote("boot-disk")
        snapshot, address = self.wait_guest(installed=True)
        require(snapshot["dvd"] == [None] or snapshot["dvd"] == [""], _("The ISO is still attached."))
        check = installed_check(snapshot, self.spec)
        result = self.guest(address, check, installed=True, capture=False)
        require(result.returncode == 0, _("Boot verification from VHDX failed."))
        self.remember_host_key()
        success(_("Installation and boot from VHDX verified. The VM remains running."))


def main():
    parser = ArgumentParser(description=_("Safely create and install a dedicated Hyper-V VM over Windows SSH."))
    parser.add_argument("mode", choices=("create", "install", "provision"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    spec = json.loads(Path(os.environ["NIXBERRY_HYPERV_MANIFEST"]).read_text())
    if spec.get("security", {}).get("mode", "none") != "none":
        from security import validate_manifest
        validate_manifest(spec)
    with tempfile.TemporaryDirectory(prefix="nixberry-hyperv-") as state:
        manager = Manager(spec, state)
        if args.mode in ("create", "provision"):
            manager.create(args.apply)
        if args.mode == "install" or (args.mode == "provision" and args.apply):
            manager.install(args.apply)


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        report_error(_("The Hyper-V operation timed out. Check the target's status and probe before retrying."))
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
