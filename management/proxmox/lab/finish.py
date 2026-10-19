#!/usr/bin/env python3
"Prepare only the data disk and staging directory of an installed Proxmox lab."

from pathlib import Path
import shlex
import subprocess
import xml.etree.ElementTree as ET

import create as lab
from messages import _, ArgumentParser, info, success, error, shell_script


def main():
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument("--apply", action="store_true", help=_("Prepare the data disk only for a verified new lab"))
    args = parser.parse_args()
    domain = ET.fromstring(lab.virsh("dumpxml", lab.NAME))
    if (domain.findtext("uuid") != lab.IDENTITY
            or domain.find("./devices/interface/mac").get("address") != lab.MAC):
        raise RuntimeError(_("The libvirt VM identity does not match the dedicated Proxmox lab."))
    info(_("Target: Proxmox lab {address}; preparing the dedicated data disk and staging directory."), address=lab.ADDRESS)
    if not args.apply:
        return 0
    # Accept a new key only on first access to our own lab verified through libvirt.
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "UserKnownHostsFile=" + str(lab.STATE / "known_hosts"), "root@" + lab.ADDRESS,
               "bash -s -- " + shlex.quote(lab.IDENTITY)]
    subprocess.run(command, input=shell_script(Path(__file__).with_name("configure.sh")), text=True, check=True)
    success(_("The Proxmox lab data resources are ready."))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, AssertionError, subprocess.SubprocessError) as exc:
        error(_("Proxmox lab finalization failed: {error}"), error=str(exc))
        raise SystemExit(1)
