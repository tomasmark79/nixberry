#!/usr/bin/env python3
"Recover from the full SD card alone; no internet, SSH or builds on another computer."

from messages import _, ArgumentParser, info, shell_script, error as report_error

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import runpy
import subprocess


def main(argv=None):
    parser = ArgumentParser(description=_("Recover from the full SD card alone; no internet, SSH or builds on another computer."))
    parser.add_argument("--target", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    parser.add_argument("--erase-existing", action="store_true")
    parser.add_argument("--key-file", help=_("Private runtime LUKS passphrase file; otherwise prompt securely"))
    parser.add_argument("--initrd-key-file", help=_("Private runtime SSH host key matching the trusted initrd public key"))
    args = parser.parse_args(argv)
    api = runpy.run_path(os.environ["NIXBERRY_RPI_MODULE"])
    try:
        manifests = json.loads(Path("/etc/nixberry-rpi-targets.json").read_text())
    except FileNotFoundError:
        raise RuntimeError(_("This command requires the prepared full-SD installation environment.")) from None
    api["require"](args.target in manifests, _("This target is not included on the installation SD."))
    target = manifests[args.target]
    encrypted = target.get("security", {}).get("mode", "none") == "luks"
    api["require"](encrypted or not (args.key_file or args.initrd_key_file), _("Runtime encryption keys require an encrypted RPi target."))
    probe = subprocess.check_output(["bash", os.environ["NIXBERRY_RPI_PROBE"], target["diskDevice"]], text=True)
    snapshot = api["parse_probe"](probe)
    api["validate"](snapshot, target, installation=True, erase_existing=args.erase_existing)
    jq = os.environ["NIXBERRY_RPI_JQ"]
    subprocess.run(["nix", "--extra-experimental-features", "nix-command", "path-info", "--offline",
                    target["systemPath"], target["diskoPath"], jq], check=True)
    info(_('RPi: {value1}; SSD: {value2}; serial: {value3}'), value1=snapshot['serial'], value2=target['diskDevice'], value3=target['diskSerial'])
    if not args.apply:
        info(_("Plan verified. Nothing changed. Erase SSD: {value1}"), value1=_("yes") if args.erase_existing else _("no"))
        return 0
    arguments = ["bash", "-s", "--", target["hardwareSerial"],
                 target["diskDevice"], target["diskSerial"], str(target["minimumDiskBytes"]),
                 target["systemPath"], target["diskoPath"], target["bootLabel"], target["rootLabel"],
                 jq + "/bin/jq", "erase-existing" if args.erase_existing else "clean-only"]
    secrets = nullcontext()
    if encrypted:
        from rpi_secrets import credentials, local
        secrets = local(credentials(target, args.key_file, args.initrd_key_file))
        arguments += ["luks", target["security"]["initrdHostKey"]]
    with secrets:
        return subprocess.run(arguments, input=shell_script(os.environ["NIXBERRY_RPI_GUEST"]),
                              text=True, check=True).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
