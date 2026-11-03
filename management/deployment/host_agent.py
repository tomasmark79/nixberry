"""Host adapter for Proxmox, VirtualBox and VMware; Python 3 on Linux and Windows.

Runs locally or over verified SSH. Accepts only declarations and fixed operations;
reset requires explicit permission and renewed ownership verification.
Partial results are never overwritten automatically.
"""

from messages import _, forward_stderr
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import subprocess
import sys
import xml.etree.ElementTree as ET

from host_backends import common, proxmox, virtualbox, vmware

# These helpers are also used by portable.py and the transferred campaign code.
from host_backends.common import (
    file_info,
    host_check,
    image_name,
    media_name,
    owned,
    pairs,
    private_directory,
    reset_reference,
    reset_tree,
    run,
    safe_addresses,
    validate,
)

BACKENDS = {"proxmox": proxmox, "virtualbox": virtualbox, "vmware": vmware}


def network_conflicts(spec, backend):
    if "networkAdapters" not in spec:
        return
    wanted = {
        common.compact_mac(item["mac"])
        for item in common.network_adapters(spec, backend)
    }
    observed = BACKENDS[backend].adapter_macs(spec)
    if wanted & {common.compact_mac(value) for value in observed}:
        raise RuntimeError(_("A declared adapter MAC is already used by another VM."))


def absent(spec, backend, directory):
    if directory.exists() or directory.is_symlink():
        raise RuntimeError(
            _(
                "The destination directory already exists; refusing to overwrite an existing or partial result."
            )
        )
    network_conflicts(spec, backend)

    BACKENDS[backend].check_absent(spec, directory)


def network_matches(info, spec, backend):
    return BACKENDS[backend].network_matches(info, spec)


def probe(spec, backend, directory, inventory=None):
    return BACKENDS[backend].probe(spec, directory, inventory)


def create(spec, backend, directory):
    marker = common.owned(directory, spec)
    installing = marker.get("method", "image") == "iso"
    image = directory / common.media_name(backend, marker)
    if image.is_symlink() or not image.is_file():
        raise RuntimeError(_("The prebuilt image is missing."))
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != marker["sha256"]:
        raise RuntimeError(_("Image checksum does not match."))
    network_conflicts(spec, backend)

    BACKENDS[backend].create(spec, directory, marker, image, installing)
    return probe(spec, backend, directory)


def inventory(spec, backend, directory, declared):
    """Incomplete inventory never proves a resource can be removed."""
    if backend in ("proxmox", "virtualbox"):
        result = BACKENDS[backend].inventory(spec, directory, declared)
        # Include transferred images from interrupted creation before registration
        # in the hypervisor, as well as Proxmox staging after import.
        seen = {
            os.path.normcase(os.path.abspath(image["path"]))
            for image in result["images"]
        }

        def walk_error(error):
            result["errors"].append(str(error))

        for base, dirs, files in os.walk(
            directory.parent, followlinks=False, onerror=walk_error
        ):
            dirs[:] = [
                name
                for name in dirs
                if not Path(base, name).is_symlink()
                and not (
                    hasattr(Path(base, name), "is_junction")
                    and Path(base, name).is_junction()
                )
            ]
            for name in files:
                path = Path(base, name)
                if (
                    not name.endswith((".qcow2", ".vdi", ".iso"))
                    or os.path.normcase(os.path.abspath(path)) in seen
                ):
                    continue
                try:
                    result["images"].append(
                        {
                            **common.file_info(path),
                            "classification": "unknown",
                            "protectedBy": [],
                            "note": _(
                                "Staging, recovery or interrupted creation; review is required."
                            ),
                        }
                    )
                except OSError as error:
                    result["errors"].append(str(error))
        return result

    return BACKENDS[backend].inventory(spec, directory, declared)


def installer_media(spec, backend, directory, *, detach=False):
    marker = common.owned(directory, spec)
    if marker.get("method", "image") != "iso":
        raise RuntimeError(_("The VM was not created for ISO installation."))
    current = probe(spec, backend, directory)
    if current["state"] not in (("shut off",) if detach else ("running", "shut off")):
        raise RuntimeError(_("The VM is not in the required state for ISO operations."))

    BACKENDS[backend].installer_media(spec, directory, marker, detach)
    return current


def reset_checks(spec, backend, directory):
    snapshot = probe(spec, backend, directory)
    if snapshot["state"] not in ("running", "shut off"):
        raise RuntimeError(
            _(
                "Reset requires an existing running or stopped VM; partial operation remnants will not be deleted."
            )
        )
    marker = common.owned(directory, spec)
    hardlink = None
    if backend == "proxmox" and marker.get("method") == "iso":
        _volume, medium = proxmox.iso_medium(spec)
        if medium.exists():
            hardlink = medium
    common.reset_tree(directory, hardlink)
    result = {
        "state": snapshot["state"],
        "directory": str(directory),
        "uuid": spec["uuid"],
    }
    if backend != "proxmox":
        result["disk"] = str(directory / common.image_name(backend))

    return BACKENDS[backend].reset_checks(spec, directory, marker, snapshot, result)


def reset_vm(spec, backend, directory, *, apply):
    plan = reset_checks(spec, backend, directory)
    if not apply:
        return {**plan, "readOnly": True}
    if plan["state"] == "running":
        BACKENDS[backend].shutdown(spec, directory)
        deadline = time.monotonic() + 120
        while probe(spec, backend, directory)["state"] != "shut off":
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    _("The VM did not shut down gracefully; reset will delete nothing.")
                )
            time.sleep(2)
    final = reset_checks(spec, backend, directory)
    if final["state"] != "shut off" or any(
        final.get(k) != plan.get(k)
        for k in ("directory", "uuid", "disk", "volumes", "media")
    ):
        raise RuntimeError(
            _("The VM or its resources changed during reset; nothing will be deleted.")
        )
    BACKENDS[backend].remove(spec, directory, final)
    if directory.exists():
        common.reset_tree(directory)
        shutil.rmtree(directory)
    if probe(spec, backend, directory)["state"] != "absent" or directory.exists():
        raise RuntimeError(_("Could not confirm removal of the VM or its directory."))
    return {"removed": spec["uuid"], "directory": str(directory)}


def dispatch(request):
    spec, backend, action = request["spec"], request["backend"], request["action"]
    if action not in (
        "probe",
        "probe-many",
        "plan",
        "prepare",
        "upload",
        "create",
        "start",
        "inventory",
        "installer-check",
        "boot-disk",
        "reset",
    ):
        raise ValueError(_("Disallowed operation."))
    if action == "reset" and (
        request.get("allowReset") is not True or type(request.get("apply")) is not bool
    ):
        raise ValueError(
            _("Reset requires explicit permission and an unambiguous apply mode.")
        )
    if action == "probe-many":
        specs = request["specs"]
        if not isinstance(specs, list) or not 1 <= len(specs) <= 128:
            raise ValueError(_("Bulk status requires 1–128 VM declarations."))
        results, query_results, checked_tools = [], {}, set()
        for candidate in specs:
            try:
                directory = common.host_check(candidate, backend, checked_tools)
                results.append(probe(candidate, backend, directory, query_results))
            except (
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                subprocess.SubprocessError,
            ) as error:
                results.append({"error": str(error)})
        return results
    directory = common.host_check(spec, backend)
    if action == "reset":
        return reset_vm(spec, backend, directory, apply=request["apply"])
    if action == "probe":
        return probe(spec, backend, directory)
    if action == "inventory":
        return inventory(spec, backend, directory, request.get("declared", []))
    method = request.get("method", "image")
    if method not in ("iso", "image"):
        raise ValueError(_("Deployment method must be iso or image."))
    if action in ("plan", "prepare"):
        absent(spec, backend, directory)
        free = shutil.disk_usage(directory.parent).free
        required = (
            request.get("imageBytes", 0)
            if backend == "proxmox"
            else max(request.get("imageBytes", 0), spec["diskGiB"] * 1024**3)
        )
        if method == "iso" and backend != "proxmox":
            required = request.get("imageBytes", 0) + spec["diskGiB"] * 1024**3
        if method == "iso":
            BACKENDS[backend].check_iso(spec, request.get("imageBytes", 0))
        if free < required:
            raise RuntimeError(
                _(
                    "There is not enough storage space for the image and declared VM capacity."
                )
            )
        if action == "prepare":
            if not re.fullmatch(r"[a-f0-9]{64}", request["sha256"]):
                raise ValueError(_("The image SHA256 is missing."))
            common.private_directory(directory)
            with (directory / "nixberry.json").open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "spec": spec,
                        "sha256": request["sha256"],
                        "imageBytes": request["imageBytes"],
                        "method": method,
                    },
                    stream,
                )
        return {
            "directory": str(directory),
            "freeBytes": free,
            "requiredBytes": required,
            "state": "absent",
            "networkAdapters": common.network_adapters(spec, backend),
        }
    if common.owned(directory, spec).get("method", "image") != method:
        raise RuntimeError(
            _("The method differs from the created VM; use the original --method.")
        )
    if action == "installer-check":
        return installer_media(spec, backend, directory)
    if action == "boot-disk":
        installer_media(spec, backend, directory, detach=True)
    if action == "upload":
        marker = common.owned(directory, spec)
        image = directory / common.media_name(backend, marker)
        digest, size = hashlib.sha256(), 0
        # Exclusive creation prevents overwrites even after an interrupted transfer.
        with image.open("xb") as stream:
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                size += len(chunk)
                if size > marker["imageBytes"]:
                    raise RuntimeError(_("The transfer exceeded the declared length."))
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != marker["sha256"] or size != marker["imageBytes"]:
            raise RuntimeError(_("The image transfer is incomplete or corrupted."))
        return {"sha256": digest.hexdigest(), "bytes": size}
    if action == "create":
        return create(spec, backend, directory)
    snapshot = probe(spec, backend, directory)
    if snapshot["state"] != "shut off":
        raise RuntimeError(
            _("Starting requires an existing powered-off VM with a verified owner.")
        )
    BACKENDS[backend].start(spec, directory)
    return {"started": True}


def entry(request):
    try:
        print(json.dumps({"ok": True, "result": dispatch(request)}, ensure_ascii=True))
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        ET.ParseError,
        subprocess.SubprocessError,
    ) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=True))
        raise SystemExit(1)
