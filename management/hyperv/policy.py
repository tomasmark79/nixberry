"""Validate declared Hyper-V instance restrictions before connecting to Windows."""

from messages import _

from pathlib import PureWindowsPath
import re
import uuid
from vm_network import adapters


def validate(spec):
    adapters(spec, "hyperv")
    if not re.fullmatch(r"(?!(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$)[a-z][a-z0-9-]{0,62}", spec.get("name", "")):
        raise RuntimeError(_("Invalid VM name: use 1–63 lowercase letters, digits and hyphens, starting with a letter; reserved Windows device names are not allowed."))
    if not re.fullmatch(r"nixberry-hyperv-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", spec.get("ownerId", "")):
        raise RuntimeError(_("A Hyper-V VM must have its own ownerId in nixberry-hyperv-UUID format."))
    if spec.get("generation") != 2 or spec.get("secureBoot") is not False:
        raise RuntimeError(_("Hyper-V requires Generation 2 with Secure Boot disabled."))
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", spec.get("mac", "")):
        raise RuntimeError(_("Invalid Hyper-V VM MAC address."))
    directory = PureWindowsPath(spec.get("storageDirectory", ""))
    if not directory.is_absolute() or not re.fullmatch(r"[A-Za-z]:", directory.drive) or ".." in directory.parts:
        raise RuntimeError(_("Hyper-V storage must be an absolute path on a local drive without parent directory components."))
    for key, minimum in (("vcpus", 1), ("memoryMiB", 256), ("diskGiB", 2)):
        if type(spec.get(key)) is not int or spec[key] < minimum:
            raise RuntimeError(_("Invalid Hyper-V capacity: {key}").format(key=key))
    if str(uuid.UUID(spec["switchId"])) != spec["switchId"]:
        raise RuntimeError(_("Switch ID must be a canonical UUID."))
