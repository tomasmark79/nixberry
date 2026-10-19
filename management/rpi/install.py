#!/usr/bin/env python3
"""Příprava nebo čistá instalace vybraného RPi5 z připnutých deklarací."""

from messages import _, ArgumentParser, info, success, warning, shell_script, forward_stderr, error as report_error

import argparse
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
    require(len(parts) == 10, _('Neúplná odpověď při kontrole RPi.'))
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
            _('Výpis disků není stromový; nelze bezpečně přiřadit oddíly.'))
    require(snapshot["architecture"] == "aarch64", _('Host není ARM64.'))
    require(snapshot["model"].startswith("Raspberry Pi 5 "), _('Host není Raspberry Pi 5.'))
    require(snapshot["serial"] == target["hardwareSerial"], _('Nesouhlasí sériové číslo RPi.'))
    require(any(link.get("address") == target["lanMac"] for link in snapshot["links"]),
            _('Nesouhlasí MAC hlavního adaptéru.'))
    disks = [disk for disk in snapshot["disks"] if disk["path"] == snapshot["device"]]
    require(len(disks) == 1 and disks[0]["type"] == "disk", _('Cíl není jednoznačný fyzický disk.'))
    disk = disks[0]
    require((disk.get("serial") or "").strip() == target["diskSerial"], _('Nesouhlasí sériové číslo disku.'))
    require(disk["size"] >= target["minimumDiskBytes"], _('Cílový disk je příliš malý.'))
    if not installation:
        return
    manifest = snapshot["installer"].get(target["target"])
    require(manifest is not None, _('Host neběží z připraveného instalačního prostředí. Lze použít pouze --stage.'))
    require(manifest["systemPath"] == target["systemPath"] and manifest["diskoPath"] == target["diskoPath"],
            _('Instalační médium obsahuje jiné připnuté výstupy.'))
    require(not any(point for node in descendants(disk) for point in node.get("mountpoints", [])),
            _('Cílový disk je připojený.'))
    if erase_existing:
        require(all(node["type"] == "part" for child in disk.get("children", []) for node in descendants(child)),
                _('Disk má podřízená zařízení jiná než běžné oddíly.'))
    else:
        require(not disk.get("children"), _('Cílový disk již má oddíly nebo podřízená zařízení.'))
        require(not snapshot["signatures"].get("signatures"), _('Cílový disk obsahuje signatury.'))
    for device in snapshot["disks"]:
        if erase_existing and device["path"] == disk["path"]:
            continue
        for node in descendants(device):
            require(node.get("partlabel") not in (target["bootLabel"], target["rootLabel"]),
                    _('Označení cílových oddílů už používá připojený disk.'))


def validate_address(address):
    parsed = ipaddress.ip_address(address)
    private = any(parsed in ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    require(parsed.version == 4 and private and not parsed.is_loopback
            and str(parsed) != "192.168.79.1", _('Nepovolená instalační adresa.'))
    return str(parsed)


def main():
    parser = ArgumentParser(description=_('Příprava nebo čistá instalace vybraného RPi5 z připnutých deklarací.'))
    parser.add_argument("--target", required=True, help=_('Název z rpi/targets/default.nix'))
    parser.add_argument("--address", help=_('Aktuální DHCP adresa instalačního prostředí místo adresy z deklarace'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--stage", action="store_true", help=_('Pouze předat výstupy do Nix store, bez instalace'))
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--erase-existing", action="store_true", help=_('Zahrnout vymazání nepřipojených oddílů do plánu; provede se pouze s --apply'))
    args = parser.parse_args()
    require(not (args.erase_existing and args.stage), _('--erase-existing nelze kombinovat s --stage.'))
    targets = json.loads(Path(os.environ["NIXBERRY_RPI_TARGETS"]).read_text())
    require(args.target in targets, _('Neznámý cíl RPi.'))
    target = targets[args.target]
    address = validate_address(args.address or target["installerAddress"])
    with tempfile.TemporaryDirectory(prefix="nixberry-rpi-ssh-") as state:
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={state}/known_hosts"]
        def ssh(command, *, input_text=None):
            result = subprocess.run(["ssh", *options, "root@" + address, command],
                                    input=input_text, text=True, capture_output=True, timeout=30, check=True)
            forward_stderr(result.stderr)
            return result
        probe = ssh(shlex.join(["bash", "-s", "--", target["diskDevice"]]),
                    input_text=Path(os.environ["NIXBERRY_RPI_PROBE"]).read_text())
        snapshot = parse_probe(probe.stdout)
        validate(snapshot, target, installation=not args.stage, erase_existing=args.erase_existing)
        info(_('RPi: {value1}; serial: {value2}; adresa: {address}'), value1=snapshot['hostname'], value2=snapshot['serial'], address=address)
        info(_('Disk: {value1}; serial: {value2}; root: {value3}'), value1=target['diskDevice'], value2=target['diskSerial'], value3=snapshot['root'])
        if not args.stage and not args.apply:
            info(_('Instalační plán ověřený, nic nebylo změněné.'))
            if args.erase_existing:
                warning(_('Plán zahrnuje smazání existujících oddílů; provede se pouze s --apply.'))
            return

        environment = os.environ | {"NIX_SSHOPTS": shlex.join(options)}
        # Přijímáme vlastní lokálně sestavené výstupy jen v tomto přenosu přes ověřené SSH.
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
            success(_('Výstupy předané a ověřené. Oddíly, aktivní systém a start RPi se nezměnily.'))
            return

        guest_arguments = ["bash", "-s", "--", target["hardwareSerial"], target["diskDevice"],
                           target["diskSerial"], str(target["minimumDiskBytes"]),
                           target["systemPath"], target["diskoPath"], target["bootLabel"],
                           target["rootLabel"], os.environ["NIXBERRY_RPI_JQ_STORE"] + "/bin/jq",
                           "erase-existing" if args.erase_existing else "clean-only"]
        subprocess.run(["ssh", *options, "root@" + address, shlex.join(guest_arguments)],
                       input=shell_script(os.environ["NIXBERRY_RPI_GUEST"]),
                       text=True, check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
