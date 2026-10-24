"""Verify the declared libvirt target and ownership before changing its resources."""

from messages import _, error as report_error

import json
import re
from pathlib import Path
import sys
import uuid


def legacy(spec):
    # The exception applies only to a previously verified test VM without a metadata marker.
    return (spec.get("name"), spec.get("uuid")) == (
        "nixberry-repro-test", "315b9a43-1653-4b2f-9cd9-790081000001")


def validate(spec):
    if spec.get("security", {}).get("mode", "none") != "none":
        from security import validate_manifest
        validate_manifest(spec)
    if spec.get("uri") != "qemu:///system":
        raise RuntimeError(_("Only local qemu:///system is allowed."))
    if not re.fullmatch(r"nixberry-[a-z0-9][a-z0-9-]{0,52}", spec["name"]):
        raise RuntimeError(_("The VM must have a safe nixberry-… name of up to 63 characters."))
    if str(uuid.UUID(spec["uuid"])) != spec["uuid"]:
        raise RuntimeError(_("UUID is not in canonical form."))
    if not legacy(spec) and spec.get("ownerId") != "nixberry-" + spec["uuid"]:
        raise RuntimeError(_("A new VM must have a declared owner tied to its UUID."))
    if spec.get("volume") != spec["name"] + ".qcow2":
        raise RuntimeError(_("The volume name must match the declared VM."))
    directory = Path(spec["storageDirectory"])
    if not directory.is_absolute() or ".." in directory.parts:
        raise RuntimeError(_("Storage must have an absolute path without parent directory components."))
    if spec["diskPath"] != str(directory / spec["volume"]):
        raise RuntimeError(_("The disk does not belong to the declared storage."))
    if not re.fullmatch(r"52:54:00(:[0-9a-f]{2}){3}", spec["mac"]):
        raise RuntimeError(_("Disallowed libvirt VM MAC address."))
    if type(spec["diskGiB"]) is not int or spec["diskGiB"] < 2:
        raise RuntimeError(_("Declared disk capacity does not match."))


def owner(root, spec):
    if "ownerId" in spec:
        node = root.find("./metadata/{urn:nixberry:deployment}owner")
        if node is None or node.get("id") != spec["ownerId"]:
            raise RuntimeError(_("The VM is missing the declared owner marker."))


if __name__ == "__main__":
    try:
        validate(json.loads(Path(sys.argv[1]).read_text()))
    except (KeyError, ValueError, RuntimeError, OSError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
