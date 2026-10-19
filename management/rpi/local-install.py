#!/usr/bin/env python3
"""Obnova ze samotné plné SD; bez internetu, SSH nebo sestavování na dalším počítači."""

from messages import _, ArgumentParser, info, shell_script, error as report_error

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess


def main(argv=None):
    parser = ArgumentParser(description=_('Obnova ze samotné plné SD; bez internetu, SSH nebo sestavování na dalším počítači.'))
    parser.add_argument("--target", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    parser.add_argument("--erase-existing", action="store_true")
    args = parser.parse_args(argv)
    api = runpy.run_path(os.environ["NIXBERRY_RPI_MODULE"])
    try:
        manifests = json.loads(Path("/etc/nixberry-rpi-targets.json").read_text())
    except FileNotFoundError:
        raise RuntimeError(_('Tento příkaz patří do připraveného instalačního prostředí plné SD.')) from None
    api["require"](args.target in manifests, _('Tento cíl není součástí instalační SD.'))
    target = manifests[args.target]
    probe = subprocess.check_output(["bash", os.environ["NIXBERRY_RPI_PROBE"], target["diskDevice"]], text=True)
    snapshot = api["parse_probe"](probe)
    api["validate"](snapshot, target, installation=True, erase_existing=args.erase_existing)
    jq = os.environ["NIXBERRY_RPI_JQ"]
    subprocess.run(["nix", "--extra-experimental-features", "nix-command", "path-info", "--offline",
                    target["systemPath"], target["diskoPath"], jq], check=True)
    info(_('RPi: {value1}; SSD: {value2}; serial: {value3}'), value1=snapshot['serial'], value2=target['diskDevice'], value3=target['diskSerial'])
    if not args.apply:
        info(_('Plán ověřený. Nic se nezměnilo. Vymazání SSD: {value1}'), value1=_('ano') if args.erase_existing else _('ne'))
        return 0
    arguments = ["bash", "-s", "--", target["hardwareSerial"],
                 target["diskDevice"], target["diskSerial"], str(target["minimumDiskBytes"]),
                 target["systemPath"], target["diskoPath"], target["bootLabel"], target["rootLabel"],
                 jq + "/bin/jq", "erase-existing" if args.erase_existing else "clean-only"]
    return subprocess.run(arguments, input=shell_script(os.environ["NIXBERRY_RPI_GUEST"]),
                          text=True, check=True).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
