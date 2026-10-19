"""Operations on disposable campaign VMs; appended to host_agent.py."""


def campaign_action(request):
    spec, backend = request["spec"], request["backend"]
    # Transferred host_agent.py functions share this namespace.
    directory = host_check(spec, backend)
    state = probe(spec, backend, directory)
    if request["action"] == "state":
        return state
    if not spec["name"].startswith("nixberry-vm-demo-test-"):
        raise ValueError(_("Campaign operations require a dedicated test name."))
    if state["state"] != "shut off":
        raise RuntimeError(_("Campaign cleanup requires a verified powered-off VM."))
    owned(directory, spec)
    if request["action"] != "cleanup":
        raise ValueError(_("Unknown campaign operation."))
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in directory.rglob("*")):
        raise RuntimeError(_("The VM directory contains a link; manual inspection is required."))
    if backend == "vmware":
        if list(directory.glob("*.lck")) or any(directory.glob("*.vmsn")):
            raise RuntimeError(_("The VM has a lock or snapshot; cleanup was stopped."))
        config = pairs((directory / (spec["name"] + ".vmx")).read_text(encoding="utf-8"))
        disks = [v for k, v in config.items() if k.endswith(".fileName") and v.lower().endswith(".vmdk")]
        if disks != [image_name(backend)]:
            raise RuntimeError(_("The VM contains unexpected disks; cleanup was stopped."))
        for snapshot in directory.glob("*.vmsd"):
            if re.search(r'snapshot\d+\.', snapshot.read_text(encoding="utf-8")):
                raise RuntimeError(_("The VM has a snapshot; cleanup was stopped."))
    elif backend == "virtualbox":
        details = pairs(run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"]))
        if details.get("SnapshotCount", "0") != "0":
            raise RuntimeError(_("The VM has a snapshot; cleanup was stopped."))
        disks = [v for k, v in details.items() if re.fullmatch(r"SATA-\d+-\d+", k)
                 and v not in ("none", "emptydrive") and not v.lower().endswith(".iso")]
        if disks != [str(directory / image_name(backend))]:
            raise RuntimeError(_("The VM contains unexpected disks; cleanup was stopped."))
        run([spec["toolPath"], "unregistervm", spec["uuid"], "--delete"])
    elif backend == "proxmox":
        snapshots = json.loads(run(["pvesh", "get", "/nodes/" + spec["node"] + "/qemu/"
                                    + str(spec["vmid"]) + "/snapshot", "--output-format", "json"]))
        if any(item["name"] != "current" for item in snapshots):
            raise RuntimeError(_("The VM has a snapshot; cleanup was stopped."))
        run(["qm", "destroy", str(spec["vmid"]), "--purge", "1"])
    else:
        raise ValueError(_("The campaign does not support cleanup for this backend."))
    # This directory belongs exclusively to the UUID; its marker matches
    # the full declaration. Do not follow redirects outside the owned directory.
    if directory.exists():
        shutil.rmtree(directory)
    return {"removed": spec["uuid"], "directory": str(directory)}
