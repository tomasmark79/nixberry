"""Náhled úklidu: žádné mazání, zastavování VM ani změny GC roots."""

from messages import _

import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from inspection import generations
from runtime import read, capabilities


def file_size(path):
    stat = Path(path).stat()
    return {"logicalBytes": stat.st_size,
            "allocatedBytes": stat.st_blocks * 512 if hasattr(stat, "st_blocks") else None,
            "modified": stat.st_mtime}


def libvirt_inventory(spec, declared):
    command = ["virsh", "--connect", spec["uri"]]
    if spec["uri"] != "qemu:///system":
        raise RuntimeError(_('Úklid libvirt podporuje pouze místní qemu:///system.'))
    references, machines, errors = {}, [], []

    def collect(xml, reason):
        for source in ET.fromstring(xml).findall(".//disk//source"):
            path = source.get("file") or source.get("dev")
            if path:
                references.setdefault(os.path.realpath(path), []).append(reason)

    for name in read(command + ["list", "--all", "--name"]).splitlines():
        try:
            domain = read(command + ["dumpxml", name])
            collect(domain, "VM:" + name)
            state = read(command + ["domstate", name])
            for snapshot in read(command + ["snapshot-list", name, "--name"]).splitlines():
                collect(read(command + ["snapshot-dumpxml", name, snapshot]), "snapshot:" + name + ":" + snapshot)
            identity = ET.fromstring(domain).findtext("uuid")
            known = any(t["spec"].get("uuid") == identity for t in declared)
            machines.append({"name": name, "state": state, "declared": known,
                             "classification": "protected" if state == "running" or known else "review",
                             "note": _('Vypnutá VM nemusí být nepoužívaná; může sloužit k obnově.')})
            allocation = 0
            for disk in ET.fromstring(domain).findall("./devices/disk"):
                if disk.get("device") != "disk":
                    continue
                device = disk.find("target")
                if device is None:
                    raise ValueError(_('Disk VM nemá cíl pro dotaz na velikost.'))
                info = read(command + ["domblkinfo", name, device.get("dev")])
                physical = re.search(r"^Physical:\s+(\d+)$", info, re.M)
                if not physical:
                    raise ValueError(_('Velikost disku VM nelze určit.'))
                allocation += int(physical[1])
            machines[-1]["allocatedBytes"] = allocation
        except (OSError, RuntimeError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
            errors.append(name + ": " + str(error))
    files = []
    volumes = []
    for line in read(command + ["vol-list", "--pool", spec["storagePool"]]).splitlines():
        if not line.strip() or line.lstrip().startswith(("Name", "---")):
            continue
        match = re.fullmatch(r"\s*(.+?)\s+(/.+?)\s*", line)
        if not match:
            errors.append(_("Neznámý řádek seznamu svazků: {line}").format(line=line))
            continue
        volumes.append(match[1])
    for volume in volumes:
        try:
            path = read(command + ["vol-path", "--pool", spec["storagePool"], volume])
            resolved = os.path.realpath(path)
            xml = ET.fromstring(read(command + ["vol-dumpxml", "--pool", spec["storagePool"], volume]))
            for item in xml.findall(".//backingStore/path"):
                filename = item.text
                if not filename:
                    raise ValueError(_('Chybí cesta podkladového obrazu.'))
                backing_path = os.path.realpath(os.path.join(os.path.dirname(path), filename))
                references.setdefault(backing_path, []).append("backing:" + path)
            sizes = {}
            for field, output in (("capacity", "logicalBytes"), ("allocation", "allocatedBytes")):
                element = xml.find(field)
                if element is None or element.get("unit", "bytes") not in ("bytes", "b"):
                    raise ValueError(_('Svazek nevrátil velikost v bajtech.'))
                sizes[output] = int(element.text)
            files.append({"path": path, "resolved": resolved, **sizes})
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            errors.append(volume + ": " + str(error))
    for file in files:
        file["protectedBy"] = references.get(file.pop("resolved"), [])
        file["classification"] = "protected" if file["protectedBy"] else "unknown" if errors else "review"
    # Stejný disk může sdílet více VM; velikosti VM nejsou celková spotřeba hostitele.
    for machine in machines:
        machine.setdefault("allocatedBytes", None)
    return {"machines": machines, "images": files, "errors": errors,
            "note": _('Rozsah: všechny VM a snapshoty místního libvirt, obrazy deklarovaného poolu. Externí konzumenti mimo libvirt nemusí být zjistitelní; review není důkaz nepoužívání.')}


def artifacts(roots):
    items, errors = [], []
    for raw in roots:
        root = Path(raw).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise RuntimeError(_('Kořen obrazů musí být adresář: {value1}').format(value1=str(root)))
        # Odkazy se nepřecházejí; oprávnění ani cizí soubory se nemění.
        def walk_error(error):
            errors.append(str(error))
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()]
            for name in files:
                path = Path(directory, name)
                if not name.endswith((".qcow2", ".vhdx", ".vdi", ".vmdk", ".img", ".img.zst", ".iso")):
                    continue
                try:
                    manifest = path.parent / "manifest.json"
                    recovery = manifest.is_file() or name.endswith((".iso", ".img.zst"))
                    items.append({"path": str(path), **file_size(path),
                        "classification": "protected" if recovery or path.is_symlink() else "unknown",
                        "protectedBy": (["recovery/export"] if recovery else []) + (["symlink"] if path.is_symlink() else []),
                        "note": _('Použití mimo tento adresář nelze z náhledu prokázat.')})
                except OSError as error:
                    errors.append(str(error))
    return {"images": items, "errors": errors}


def preview(registry, names, roots=()):
    report = {"readOnly": True, "targets": {}, "hosts": {}, "artifacts": artifacts(roots),
              "note": _('Náhled nic nemaže. Vypnutí ani stáří neprokazuje nepoužívání. Velikosti sdílených disků a generací nesčítej jako uvolnitelné místo.')}
    for name in names:
        target = registry[name]
        try:
            report["targets"][name] = generations(target)
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            report["targets"][name] = {"error": str(error)}
        host = target["host"]
        if host in report["hosts"] or not capabilities(target)["hypervisor"]:
            continue
        try:
            if target["backend"] == "libvirt":
                declared = [t for t in registry.values() if t["host"] == host]
                report["hosts"][host] = libvirt_inventory(target["spec"], declared)
            elif target["backend"] in ("proxmox", "virtualbox", "vmware"):
                from portable import inventory
                report["hosts"][host] = inventory(target, registry)
            elif target["backend"] == "hyperv":
                data = json.loads(read([target["commands"]["probe"]]))
                if data.get("hypervError") or data.get("computerName", "").lower() != target["spec"]["windowsComputerName"].lower():
                    raise RuntimeError((data.get("hypervError") or _('Nesouhlasí Windows hostitel.')))
                report["hosts"][host] = {"machines": data["vms"], "images": data.get("images", []),
                    "errors": data.get("inventoryErrors", []),
                    "note": _('Zahrnuje běžící i vypnuté VM a jejich checkpointy; cizí disky nejsou kandidáty k mazání.')}
        except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
            report["hosts"][host] = {"error": str(error)}
    return report


def failed(report):
    return bool(report["artifacts"]["errors"] or any(
        item.get("error") or item.get("errors") or item.get("sizeError")
        for section in ("targets", "hosts") for item in report[section].values()))
