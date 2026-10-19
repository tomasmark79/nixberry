"""Ověření deklarovaného libvirt cíle a vlastnictví před zásahy do jeho prostředků."""

from messages import _, error as report_error

import json
import re
from pathlib import Path
import sys
import uuid


def legacy(spec):
    # Výjimka patří pouze už ověřené testovací VM bez metadata značky.
    return (spec.get("name"), spec.get("uuid")) == (
        "nixberry-repro-test", "315b9a43-1653-4b2f-9cd9-790081000001")


def validate(spec):
    if spec.get("uri") != "qemu:///system":
        raise RuntimeError(_('Povolený je pouze místní qemu:///system.'))
    if not re.fullmatch(r"nixberry-[a-z0-9][a-z0-9-]{0,52}", spec["name"]):
        raise RuntimeError(_('VM musí mít bezpečný název nixberry-… do 63 znaků.'))
    if str(uuid.UUID(spec["uuid"])) != spec["uuid"]:
        raise RuntimeError(_('UUID není v kanonickém tvaru.'))
    if not legacy(spec) and spec.get("ownerId") != "nixberry-" + spec["uuid"]:
        raise RuntimeError(_('Nová VM musí mít deklarovaného vlastníka svázaného s UUID.'))
    if spec.get("volume") != spec["name"] + ".qcow2":
        raise RuntimeError(_('Název svazku musí odpovídat deklarované VM.'))
    directory = Path(spec["storageDirectory"])
    if not directory.is_absolute() or ".." in directory.parts:
        raise RuntimeError(_('Úložiště musí mít absolutní cestu bez nadřazených složek.'))
    if spec["diskPath"] != str(directory / spec["volume"]):
        raise RuntimeError(_('Disk nepatří do deklarovaného úložiště.'))
    if not re.fullmatch(r"52:54:00(:[0-9a-f]{2}){3}", spec["mac"]):
        raise RuntimeError(_('Nepovolená MAC libvirt VM.'))
    if type(spec["diskGiB"]) is not int or spec["diskGiB"] < 2:
        raise RuntimeError(_('Nesouhlasí deklarovaná kapacita disku.'))


def owner(root, spec):
    if "ownerId" in spec:
        node = root.find("./metadata/{urn:nixberry:deployment}owner")
        if node is None or node.get("id") != spec["ownerId"]:
            raise RuntimeError(_('VM nemá deklarovanou značku vlastníka.'))


if __name__ == "__main__":
    try:
        validate(json.loads(Path(sys.argv[1]).read_text()))
    except (KeyError, ValueError, RuntimeError, OSError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
