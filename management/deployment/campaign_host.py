"""Operace postradatelných VM kampaně; připojuje se za zdroj host_agent.py."""


def campaign_action(request):
    spec, backend = request["spec"], request["backend"]
    # Funkce host_agent.py jsou při přenosu ve stejném jmenném prostoru.
    directory = host_check(spec, backend)
    state = probe(spec, backend, directory)
    if request["action"] == "state":
        return state
    if not spec["name"].startswith("nixberry-vm-demo-test-"):
        raise ValueError(_("Operace kampaně vyžaduje vlastní testovací jméno."))
    if state["state"] != "shut off":
        raise RuntimeError(_("Úklid kampaně vyžaduje ověřenou vypnutou VM."))
    owned(directory, spec)
    if request["action"] != "cleanup":
        raise ValueError(_("Neznámá operace kampaně."))
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in directory.rglob("*")):
        raise RuntimeError(_("Adresář VM obsahuje odkaz; ruční kontrola je nutná."))
    if backend == "vmware":
        if list(directory.glob("*.lck")) or any(directory.glob("*.vmsn")):
            raise RuntimeError(_("VM má zámek nebo snapshot; úklid byl zastaven."))
        config = pairs((directory / (spec["name"] + ".vmx")).read_text(encoding="utf-8"))
        disks = [v for k, v in config.items() if k.endswith(".fileName") and v.lower().endswith(".vmdk")]
        if disks != [image_name(backend)]:
            raise RuntimeError(_("VM obsahuje neočekávané disky; úklid byl zastaven."))
        for snapshot in directory.glob("*.vmsd"):
            if re.search(r'snapshot\d+\.', snapshot.read_text(encoding="utf-8")):
                raise RuntimeError(_("VM má snapshot; úklid byl zastaven."))
    elif backend == "virtualbox":
        details = pairs(run([spec["toolPath"], "showvminfo", spec["uuid"], "--machinereadable"]))
        if details.get("SnapshotCount", "0") != "0":
            raise RuntimeError(_("VM má snapshot; úklid byl zastaven."))
        disks = [v for k, v in details.items() if re.fullmatch(r"SATA-\d+-\d+", k)
                 and v not in ("none", "emptydrive") and not v.lower().endswith(".iso")]
        if disks != [str(directory / image_name(backend))]:
            raise RuntimeError(_("VM obsahuje neočekávané disky; úklid byl zastaven."))
        run([spec["toolPath"], "unregistervm", spec["uuid"], "--delete"])
    elif backend == "proxmox":
        snapshots = json.loads(run(["pvesh", "get", "/nodes/" + spec["node"] + "/qemu/"
                                    + str(spec["vmid"]) + "/snapshot", "--output-format", "json"]))
        if any(item["name"] != "current" for item in snapshots):
            raise RuntimeError(_("VM má snapshot; úklid byl zastaven."))
        run(["qm", "destroy", str(spec["vmid"]), "--purge", "1"])
    else:
        raise ValueError(_("Úklid tohoto backendu kampaň nepodporuje."))
    # Adresář vznikl výhradně pro toto UUID a marker odpovídá celé deklaraci.
    # Neprocházíme přesměrování, která by mohla opustit vlastněný adresář.
    if directory.exists():
        shutil.rmtree(directory)
    return {"removed": spec["uuid"], "directory": str(directory)}
