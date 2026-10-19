#!/usr/bin/env python3
"""Připraví pouze datový disk a staging již nainstalovaného Proxmox labu."""

from pathlib import Path
import shlex
import subprocess
import xml.etree.ElementTree as ET

import create as lab
from messages import _, ArgumentParser, info, success, error, shell_script


def main():
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument("--apply", action="store_true", help=_("Připravit datový disk pouze ověřeného nového labu"))
    args = parser.parse_args()
    domain = ET.fromstring(lab.virsh("dumpxml", lab.NAME))
    if (domain.findtext("uuid") != lab.IDENTITY
            or domain.find("./devices/interface/mac").get("address") != lab.MAC):
        raise RuntimeError(_("Identita libvirt VM neodpovídá vlastnímu Proxmox labu."))
    info(_("Cíl: Proxmox lab {address}; připravení vyhrazeného datového disku a stagingu."), address=lab.ADDRESS)
    if not args.apply:
        return 0
    # Nový klíč přijímáme pouze při prvním spojení do přes libvirt ověřeného vlastního labu.
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "UserKnownHostsFile=" + str(lab.STATE / "known_hosts"), "root@" + lab.ADDRESS,
               "bash -s -- " + shlex.quote(lab.IDENTITY)]
    subprocess.run(command, input=shell_script(Path(__file__).with_name("configure.sh")), text=True, check=True)
    success(_("Datové prostředky Proxmox labu jsou připravené."))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, AssertionError, subprocess.SubprocessError) as exc:
        error(_("Dokončení Proxmox labu selhalo: {error}"), error=str(exc))
        raise SystemExit(1)
