#!/usr/bin/env python3
"Diagnose loading the RPi recovery kernel without rebooting or writing partitions."

from messages import _, ArgumentParser, info, success, error as report_error

import argparse
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tempfile

API = runpy.run_path(os.environ["NIXBERRY_RPI_MODULE"])
require = API["require"]


def kernel_command_line(current, params, system):
    preserved = [value for value in shlex.split(current) if value not in ("ro", "rw") and not
                 value.startswith(("init=", "root=", "rdinit=", "initrd=", "resume=", "systemd.unit="))]
    return shlex.join(preserved + params + ["init=" + system + "/init"])


def main():
    parser = ArgumentParser(description=_("Diagnose loading the RPi recovery kernel without rebooting or writing partitions."))
    parser.add_argument("--target", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--load-check", action="store_true", help=_("Load and unload the kernel without rebooting"))
    args = parser.parse_args()
    targets = json.loads(Path(os.environ["NIXBERRY_RPI_TARGETS"]).read_text())
    recoveries = json.loads(Path(os.environ["NIXBERRY_RECOVERY_SPEC"]).read_text())
    require(args.target in targets and args.target in recoveries, _("Unknown RPi recovery target."))
    spec = recoveries[args.target]
    target = targets[args.target]
    address = API["validate_address"](target["installerAddress"])
    with tempfile.TemporaryDirectory(prefix="nixberry-recovery-ssh-") as state:
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={state}/known_hosts"]

        def ssh(command, *, input_text=None, check=True):
            return subprocess.run(["ssh", *options, "root@" + address, command],
                                  input=input_text, text=True, capture_output=True,
                                  timeout=25, check=check)

        snapshot = API["parse_probe"](ssh(
            shlex.join(["bash", "-s", "--", target["diskDevice"]]),
            input_text=Path(os.environ["NIXBERRY_RPI_PROBE"]).read_text()).stdout)
        API["validate"](snapshot, target, installation=False)
        require(ssh("readlink -f /run/current-system").stdout.strip() == target["systemPath"],
                _("The active system is not the expected pinned system."))
        require(ssh("cat /sys/kernel/kexec_loaded").stdout.strip() == "0",
                _("Another kernel is already loaded; it will not be overwritten."))
        available = int(ssh("awk '/MemAvailable:/ { printf \"%.0f\\n\", $2 * 1024 }' /proc/meminfo").stdout.strip())
        needed = (Path(spec["tree"]) / "initrd.gz").stat().st_size * 2 + 512 * 1024**2
        require(available >= needed, _("Not enough free RAM for the loading check."))
        info(_("RPi verified: {value1}; recovery: {value2}"), value1=snapshot['serial'], value2=spec['tree'])
        info(_("MemAvailable={available}; conservative requirement={needed}"), available=available, needed=needed)
        if not args.load_check:
            info(_("Plan verified. No copying, kernel loading or reboot."))
            return
        environment = os.environ | {"NIX_SSHOPTS": shlex.join(options)}
        subprocess.run(["nix", "--extra-experimental-features", "nix-command", "copy",
                        "--no-check-sigs", "--to", "ssh-ng://root@" + address, spec["tree"]],
                       env=environment, check=True)
        cmdline = kernel_command_line(ssh("cat /proc/cmdline").stdout, spec["params"], spec["system"])
        load = shlex.join(["kexec", "-s", "-l", spec["tree"] + "/bzImage",
                          "--initrd=" + spec["tree"] + "/initrd.gz", "--command-line=" + cmdline])
        try:
            ssh(load)
            require(ssh("cat /sys/kernel/kexec_loaded").stdout.strip() == "1", _("The kernel is not loaded."))
        finally:
            ssh("if test \"$(cat /sys/kernel/kexec_loaded)\" = 1; then kexec -s -u; fi", check=False)
        require(ssh("cat /sys/kernel/kexec_loaded").stdout.strip() == "0", _("The kernel was not unloaded."))
        success(_("Loading and unloading verified. The RPi was not rebooted."))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit((error.stderr or str(error)))
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
