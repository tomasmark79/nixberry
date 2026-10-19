#!/usr/bin/env python3
"""Odstranění pouze vyhrazené testovací VM a jejího disku; výchozí režim nic nemění."""

from messages import _, ArgumentParser, info, error as report_error

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
import policy
from storage import reject_shared, reject_shared_domains


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_target(domain_xml, volume_xml, manifest):
    root = ET.fromstring(domain_xml)
    policy.owner(root, manifest)
    volume = ET.fromstring(volume_xml)
    require(root.findtext("name") == manifest["name"], _('Nesouhlasí název VM.'))
    require(root.findtext("uuid") == manifest["uuid"], _('Nesouhlasí UUID VM.'))
    disks = root.findall("./devices/disk[@device='disk']")
    require(len(disks) == 1, _('VM nemá právě jeden disk.'))
    source = disks[0].find("source")
    require(source is not None and source.get("file") == manifest["diskPath"], _('Nesouhlasí cesta disku.'))
    require(disks[0].find("target").get("dev") == "vda", _('Cílový disk není vda.'))
    require(volume.findtext("name") == manifest["volume"], _('Nesouhlasí název svazku.'))
    require(volume.findtext("target/path") == manifest["diskPath"], _('Nesouhlasí cesta svazku.'))
    require(volume.findtext("capacity") == str(manifest["diskGiB"] * 1024**3), _('Nesouhlasí velikost svazku.'))
    require(volume.find("target/format").get("type") == "qcow2", _('Svazek není qcow2.'))
    require(volume.find("backingStore/path") is None, _('Svazek používá podkladový obraz.'))
    nvram = root.findtext("./os/nvram")
    require(nvram == "/var/lib/libvirt/qemu/nvram/" + manifest["name"] + "_VARS.fd",
            _('Nesouhlasí cesta NVRAM testovací VM.'))
    return manifest["diskPath"], nvram


def main():
    parser = ArgumentParser(description=_('Odstranění pouze vyhrazené testovací VM a jejího disku; výchozí režim nic nemění.'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(Path(os.environ["NIXBERRY_MANIFEST"]).read_text())
    policy.validate(manifest)
    require(manifest.get("allowReset", False) is True, _('Odstranění této instance není deklarativně povolené.'))
    require(manifest.get("ownerId") == "nixberry-" + manifest["uuid"],
            _('Reset vyžaduje deklarovaného vlastníka NixBerry; samotné známé UUID nestačí.'))
    require(manifest["volume"] == manifest["name"] + ".qcow2",
            _('Svazek nemá vyhrazený název testovací VM.'))

    def virsh(*arguments):
        return subprocess.check_output(
            ["virsh", "--connect", manifest["uri"], *arguments], text=True, timeout=20,
            env={**os.environ, "LC_ALL": "C"}
        ).strip()

    def validate():
        volume = virsh("vol-dumpxml", manifest["volume"], "--pool", manifest["storagePool"])
        disk, nvram = validate_target(virsh("dumpxml", manifest["name"], "--inactive"), volume, manifest)
        for filename in (disk, nvram):
            path = Path(filename)
            require(path.is_file() and not path.is_symlink() and not path.is_mount()
                    and path.stat().st_nlink == 1 and not any(p.is_symlink() for p in path.parents),
                    _('Disk nebo NVRAM chybí, je odkazem nebo sdíleným souborem; reset odmítám.'))
        image = json.loads(subprocess.check_output(
            ["qemu-img", "info", "-U", "--output=json", disk], text=True, timeout=20))
        require(image.get("format") == "qcow2" and image.get("virtual-size") == manifest["diskGiB"] * 1024**3
                and not image.get("backing-filename") and not image.get("full-backing-filename")
                and not image.get("snapshots"),
                _('Skutečný obraz má jiný formát, kapacitu, podklad nebo interní snapshot; reset odmítám.'))
        state = virsh("domstate", manifest["name"])
        require(state in ("running", "shut off"), _('VM není v podporovaném stavu.'))
        if state == "running":
            validate_target(virsh("dumpxml", manifest["name"]), volume, manifest)
        require(not virsh("snapshot-list", manifest["name"], "--name"),
                _('VM má snapshoty; odmítám odstranění.'))
        reject_shared_domains(virsh, manifest["name"], disk, nvram)
        return state, disk, nvram

    state, disk, nvram = validate()
    info(_('VM: {value1}; UUID: {value2}; stav: {state}'), value1=manifest['name'], value2=manifest['uuid'], state=state)
    info(_('Odstranit pouze: {disk} a {nvram}'), disk=disk, nvram=nvram)
    if not args.apply:
        info(_('Plán odstranění ověřený. Nic nebylo změněné.'))
        return
    if state == "running":
        virsh("shutdown", manifest["name"])
        deadline = time.monotonic() + 120
        while virsh("domstate", manifest["name"]) != "shut off":
            require(time.monotonic() < deadline, _('VM se nevypnula; nic nemažeme.'))
            time.sleep(2)
    state, unused, unused = validate()
    require(state == "shut off", _('VM není vypnutá; nic nemažeme.'))
    virsh("undefine", manifest["name"], "--nvram")
    virsh("vol-delete", manifest["volume"], "--pool", manifest["storagePool"])
    require(not os.path.lexists(disk), _('Soubor disku po odstranění stále existuje.'))
    require(not os.path.lexists(nvram), _('NVRAM po odstranění stále existuje.'))
    info(_('Odstraněna pouze vyhrazená testovací VM, její disk a NVRAM.'))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError, ET.ParseError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
