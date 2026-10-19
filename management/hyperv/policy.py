"""Omezení deklarované Hyper-V instance před prvním připojením na Windows."""

from messages import _

from pathlib import PureWindowsPath
import re
import uuid


def validate(spec):
    if not re.fullmatch(r"nixberry-[a-z0-9][a-z0-9-]{0,52}", spec.get("name", "")):
        raise RuntimeError(_('Hyper-V VM musí mít bezpečný název nixberry-….'))
    if not re.fullmatch(r"nixberry-hyperv-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", spec.get("ownerId", "")):
        raise RuntimeError(_('Hyper-V VM musí mít vlastní ownerId ve tvaru nixberry-hyperv-UUID.'))
    if spec.get("generation") != 2 or spec.get("secureBoot") is not False:
        raise RuntimeError(_('Hyper-V vyžaduje Generation 2 a vypnutý Secure Boot.'))
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", spec.get("mac", "")):
        raise RuntimeError(_('Neplatná MAC Hyper-V VM.'))
    directory = PureWindowsPath(spec.get("storageDirectory", ""))
    if not directory.is_absolute() or not re.fullmatch(r"[A-Za-z]:", directory.drive) or ".." in directory.parts:
        raise RuntimeError(_('Hyper-V úložiště musí být absolutní cesta na lokálním disku bez nadřazených složek.'))
    for key, minimum in (("vcpus", 1), ("memoryMiB", 256), ("diskGiB", 2)):
        if type(spec.get(key)) is not int or spec[key] < minimum:
            raise RuntimeError(_('Neplatná kapacita Hyper-V: {key}').format(key=key))
    if str(uuid.UUID(spec["switchId"])) != spec["switchId"]:
        raise RuntimeError(_('Switch ID musí být kanonické UUID.'))
