#!/usr/bin/env python3
"Prepare or cleanly install a selected RPi5 from pinned declarations."

from messages import _, ArgumentParser, info, success, warning, shell_script, forward_stderr, error as report_error

import argparse
from contextlib import nullcontext
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parse_probe(output):
    parts = output.split("\x1e")
    require(len(parts) == 10, _("Incomplete RPi check response."))
    return {
        "architecture": parts[0].strip(), "model": parts[1].strip(),
        "serial": parts[2].strip(), "hostname": parts[3].strip(),
        "root": parts[4].strip(), "links": json.loads(parts[5]),
        "disks": json.loads(parts[6])["blockdevices"], "device": parts[7].strip(),
        "installer": json.loads(parts[8]), "signatures": json.loads(parts[9]),
    }


def descendants(device):
    yield device
    for child in device.get("children", []):
        yield from descendants(child)


def validate(snapshot, target, *, installation, erase_existing=False):
    require(not any(device["type"] == "part" for device in snapshot["disks"]),
            _("The disk listing is not a tree; partitions cannot be assigned safely."))
    require(snapshot["architecture"] == "aarch64", _("The guest is not ARM64."))
    require(snapshot["model"].startswith("Raspberry Pi 5 "), _("The guest is not a Raspberry Pi 5."))
    require(snapshot["serial"] == target["hardwareSerial"], _("RPi serial number does not match."))
    require(any(link.get("address") == target["lanMac"] for link in snapshot["links"]),
            _("Primary adapter MAC address does not match."))
    for adapter in target.get("network", {}).get("interfaces", {}).values():
        require(any(link.get("address", "").lower() == adapter["mac"] for link in snapshot["links"]),
                _("The RPi network adapter does not match the declaration."))
    disks = [disk for disk in snapshot["disks"] if disk["path"] == snapshot["device"]]
    require(len(disks) == 1 and disks[0]["type"] == "disk", _("The target is not an unambiguous physical disk."))
    disk = disks[0]
    require((disk.get("serial") or "").strip() == target["diskSerial"], _("Disk serial number does not match."))
    require(disk["size"] >= target["minimumDiskBytes"], _("The target disk is too small."))
    if not installation:
        return
    manifest = snapshot["installer"].get(target["target"])
    require(manifest is not None, _("The guest is not running the prepared installation environment. Only --stage is available."))
    require(manifest["systemPath"] == target["systemPath"] and manifest["diskoPath"] == target["diskoPath"],
            _("The installation media contains different pinned outputs."))
    if target.get("security", {}).get("mode", "none") != "none":
        require(manifest.get("security") == target["security"],
                _("The installation media contains a different RPi encryption policy or SSH identity."))
    require(not any(point for node in descendants(disk) for point in node.get("mountpoints", [])),
            _("The target disk is mounted."))
    if erase_existing:
        require(all(node["type"] == "part" for child in disk.get("children", []) for node in descendants(child)),
                _("The disk has child devices other than regular partitions."))
    else:
        require(not disk.get("children"), _("The target disk already has partitions or child devices."))
        require(not snapshot["signatures"].get("signatures"), _("The target disk contains signatures."))
    for device in snapshot["disks"]:
        if erase_existing and device["path"] == disk["path"]:
            continue
        for node in descendants(device):
            require(node.get("partlabel") not in (target["bootLabel"], target["rootLabel"]),
                    _("A mounted disk already uses the target partition labels."))


def validate_address(address):
    parsed = ipaddress.ip_address(address)
    private = any(parsed in ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    require(parsed.version == 4 and private and not parsed.is_loopback
            and str(parsed) != "192.168.79.1", _("Disallowed installation address."))
    return str(parsed)


def main():
    parser = ArgumentParser(description=_("Prepare or cleanly install a selected RPi5 from pinned declarations."))
    parser.add_argument("--target", required=True, help=_("Name from rpi/targets/default.nix"))
    parser.add_argument("--address", help=_("Current DHCP address of the installation environment, overriding the declaration"))
    parser.add_argument("--key-file", help=_("Private runtime LUKS passphrase file; otherwise prompt securely"))
    parser.add_argument("--initrd-key-file", help=_("Private runtime SSH host key matching the trusted initrd public key"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--stage", action="store_true", help=_("Only transfer outputs to the Nix store, without installation"))
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--erase-existing", action="store_true", help=_("Include erasing unmounted partitions in the plan; only executed with --apply"))
    args = parser.parse_args()
    require(not (args.erase_existing and args.stage), _("--erase-existing cannot be combined with --stage."))
    targets = json.loads(Path(os.environ["NIXBERRY_RPI_TARGETS"]).read_text())
    require(args.target in targets, _("Unknown RPi target."))
    target = targets[args.target]
    encrypted = target.get("security", {}).get("mode", "none") == "luks"
    require(encrypted or not (args.key_file or args.initrd_key_file), _("Runtime encryption keys require an encrypted RPi target."))
    address = validate_address(args.address or target["installerAddress"])
    with tempfile.TemporaryDirectory(prefix="nixberry-rpi-ssh-") as state:
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={state}/known_hosts"]
        if encrypted:
            key = target["security"].get("recoveryHostKey")
            require(key, _("Set security.recoveryHostKey to the trusted recovery medium SSH public key before connecting."))
            alias = "nixberry-recovery-" + target["hardwareSerial"]
            Path(state, "known_hosts").write_text(alias + " " + key + "\n")
            options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                       "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlias=" + alias,
                       "-o", f"UserKnownHostsFile={state}/known_hosts", "-o", "GlobalKnownHostsFile=/dev/null",
                       "-o", "KnownHostsCommand=none", "-o", "VerifyHostKeyDNS=no", "-o", "UpdateHostKeys=no",
                       "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "ControlMaster=no", "-o", "ControlPath=none",
                       "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes"]
        def ssh(command, *, input_text=None, input_bytes=None):
            result = subprocess.run(["ssh", *options, "root@" + address, command],
                                    input=input_bytes if input_bytes is not None else input_text,
                                    text=input_bytes is None, capture_output=True, timeout=30, check=True)
            if input_bytes is not None:
                result.stdout, result.stderr = result.stdout.decode(), result.stderr.decode()
            forward_stderr(result.stderr)
            return result
        probe = ssh(shlex.join(["bash", "-s", "--", target["diskDevice"]]),
                    input_text=Path(os.environ["NIXBERRY_RPI_PROBE"]).read_text())
        snapshot = parse_probe(probe.stdout)
        validate(snapshot, target, installation=not args.stage, erase_existing=args.erase_existing)
        info(_("RPi: {value1}; serial: {value2}; address: {address}"), value1=snapshot['hostname'], value2=snapshot['serial'], address=address)
        info(_('Disk: {value1}; serial: {value2}; root: {value3}'), value1=target['diskDevice'], value2=target['diskSerial'], value3=snapshot['root'])
        if not args.stage and not args.apply:
            info(_("Installation plan verified; nothing was changed."))
            if args.erase_existing:
                warning(_("The plan includes erasing existing partitions; this only happens with --apply."))
            return

        environment = os.environ | {"NIX_SSHOPTS": shlex.join(options)}
        # Accept locally built outputs only for this transfer over verified SSH.
        subprocess.run(
            ["nix", "--extra-experimental-features", "nix-command", "copy",
             "--no-check-sigs", "--to", "ssh-ng://root@" + address, target["systemPath"], target["diskoPath"],
             os.environ["NIXBERRY_RPI_JQ_STORE"]],
            env=environment, check=True
        )
        verify = shlex.join(["nix", "--extra-experimental-features", "nix-command",
                             "path-info", "--offline", target["systemPath"], target["diskoPath"]])
        print(ssh(verify).stdout, end="", flush=True)
        if args.stage:
            success(_("Outputs transferred and verified. Partitions, the active system and RPi boot state were not changed."))
            return

        guest_arguments = ["bash", "-s", "--", target["hardwareSerial"], target["diskDevice"],
                           target["diskSerial"], str(target["minimumDiskBytes"]),
                           target["systemPath"], target["diskoPath"], target["bootLabel"],
                           target["rootLabel"], os.environ["NIXBERRY_RPI_JQ_STORE"] + "/bin/jq",
                           "erase-existing" if args.erase_existing else "clean-only"]
        secrets = nullcontext()
        if encrypted:
            from rpi_secrets import credentials, remote
            values = credentials(target, args.key_file, args.initrd_key_file)
            secrets = remote(ssh, values)
            guest_arguments += ["luks", target["security"]["initrdHostKey"]]
        with secrets:
            subprocess.run(["ssh", *options, "root@" + address, shlex.join(guest_arguments)],
                           input=shell_script(os.environ["NIXBERRY_RPI_GUEST"]),
                           text=True, check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
