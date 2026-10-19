"""Čerstvý stav souběžně po hostitelích, se sdílením přehledu v jediném dotazu."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import xml.etree.ElementTree as ET

from messages import _
from runtime import addresses, direct_connection, hyperv_status, libvirt_status, read
from portable import rpc


FAILURES = (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError)


def failure(error):
    return {"state": "error", "addresses": [], "error": str(error)}


def inspect_group(registry, names, inspect):
    first = registry[names[0]]
    backend = first["backend"]
    if (first.get("offline") or direct_connection(first) or backend not in ("libvirt", "proxmox", "virtualbox", "vmware", "hyperv")
            or (backend == "libvirt" and first["spec"]["uri"] != "qemu:///system")):
        results = {}
        for name in names:
            try:
                results[name] = inspect(registry[name])
            except FAILURES as error:
                results[name] = failure(error)
        return results
    if backend == "libvirt":
        machines = read(["virsh", "--connect", "qemu:///system", "list", "--all", "--name"]).splitlines()
        results = {}
        for name in names:
            try:
                results[name] = libvirt_status(registry[name], machines)
            except FAILURES as error:
                results[name] = failure(error)
        return results
    if backend == "hyperv":
        command = [first["commands"]["probe"], "--status-only"]
        for name in names:
            command.extend(["--vm-name", registry[name]["spec"]["name"]])
        data = json.loads(read(command))
        results = {}
        for name in names:
            try:
                results[name] = hyperv_status(registry[name], data)
            except FAILURES as error:
                results[name] = failure(error)
        return results
    results = {}
    for start in range(0, len(names), 8):
        chunk = names[start:start + 8]
        snapshots = rpc(registry[chunk[0]], "probe-many", specs=[registry[name]["spec"] for name in chunk],
                        timeout=15 + 10 * len(chunk))
        if not isinstance(snapshots, list) or len(snapshots) != len(chunk):
            raise RuntimeError(_('Hostitel nevrátil stav všech požadovaných VM.'))
        for name, snapshot in zip(chunk, snapshots):
            if "error" in snapshot:
                results[name] = failure(snapshot["error"])
            else:
                results[name] = {**snapshot, "addresses": addresses(snapshot["addresses"],
                                 excluded=(registry[name]["spec"].get("sshHost", ""),))}
    return results


def collect_status(registry, names, inspect, on_result=None):
    groups = {}
    for name in names:
        target = registry[name]
        # Přímé SSH a offline deklarace nemají společnou správu hypervizoru.
        key = (target["backend"], target["host"]) if not target.get("offline") else ("direct", name)
        groups.setdefault(key, []).append(name)
    results = {}
    with ThreadPoolExecutor(max_workers=min(4, len(groups)) or 1) as executor:
        pending = {executor.submit(inspect_group, registry, group, inspect): group for group in groups.values()}
        for future in as_completed(pending):
            group = pending[future]
            try:
                snapshots = future.result()
            except FAILURES as error:
                snapshots = {name: failure(error) for name in group}
            for name in group:
                target = registry[name]
                metadata = {"host": target["host"], "capabilities": target["capabilities"],
                            "hypervisor": target["host"] if target["capabilities"]["hypervisor"] else None}
                results[name] = {**metadata, **snapshots[name]}
                if on_result:
                    on_result(name, results[name])
    return {name: results[name] for name in names}
