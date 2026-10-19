#!/usr/bin/env python3
"""Regrese evidence cílů v kopiích receptu; bez připojení k hostitelům nebo změn VM."""

from messages import _, ArgumentParser, info, error as report_error

import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def nix(value):
    if isinstance(value, dict):
        return "{ " + " ".join(nix(key) + " = " + nix(item) + ";" for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[ " + " ".join(map(nix, value)) + " ]"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("${", "\\${")
    return json.dumps(value)


def evaluate(expression):
    result = subprocess.run(["nix", "eval", "--impure", "--json", "--no-write-lock-file",
                             "--option", "eval-cache", "false", "--expr", expression],
                            text=True, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:])
    return json.loads(result.stdout)


def registry(recipe):
    return evaluate("import " + str(recipe / "management/deployment"))


def summary(recipe):
    return evaluate('''let f = builtins.getFlake ''' + nix("path:" + str(recipe)) + '''; in {
      targets = builtins.attrNames f.nixberryDeployment.targets;
      hosts = builtins.attrNames f.nixberryDeployment.hosts;
      tool = f.packages.x86_64-linux.nixberry.outPath;
      manifest = builtins.fromJSON (builtins.unsafeDiscardStringContext f.packages.x86_64-linux.nixberry.manifest.text);
      systems = builtins.mapAttrs (_: system: {
        path = system.config.system.build.toplevel.outPath;
        hostname = system.config.networking.hostName;
      }) f.nixberrySystems;
      vmTools = builtins.mapAttrs (_: tools: builtins.fromJSON (builtins.unsafeDiscardStringContext tools.manifest.text)) f.nixberryTools;
      images = builtins.mapAttrs (_: image: image.drvPath) f.nixberryImages;
      packages = builtins.mapAttrs (_: package: package.drvPath) f.packages.x86_64-linux;
      recoveryTargets = builtins.attrNames f.nixberryRpiRecoveries;
    }''')


def main():
    ArgumentParser(description=_(__doc__)).parse_args()
    recipe = Path(__file__).resolve().parents[2]
    preferred = Path("/home/shared/tmp")
    root = Path(tempfile.mkdtemp(prefix="nixberry-management-check-",
                                 dir=preferred if preferred.is_dir() else None))
    info(_('Kopie a výsledky: {value1}'), value1=str(root))
    raw = evaluate("import " + str(recipe / "instances"))
    initial = registry(recipe)
    records = {}

    def copy(name):
        destination = root / name
        shutil.copytree(recipe, destination, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".git", "result", "result-*", "*.log"))
        return destination

    def remove_vm(destination, predicate):
        for name, target in initial["instances"].items():
            if predicate(name, target):
                (destination / "instances" / (name + ".nix")).unlink()

    def remove_rpi(destination):
        for path in (destination / "management/rpi/targets").glob("*.nix"):
            if path.name != "default.nix":
                path.unlink()

    for case in ("empty-all", "no-vms", "no-rpi", "no-originals", "no-hyperv", "only-hyperv", "renamed", "two-hyperv", "portable"):
        destination = copy(case)
        if case in ("empty-all", "no-vms"):
            remove_vm(destination, lambda *_: True)
        if case in ("empty-all", "no-rpi", "no-originals", "only-hyperv"):
            remove_rpi(destination)
        if case == "empty-all":
            (destination / "hosts.local.nix").write_text("{}\n")
        if case == "no-originals":
            remove_vm(destination, lambda name, _: name in ("nixon-test", "musicbox-test"))
        if case == "no-hyperv":
            remove_vm(destination, lambda _, target: target["backend"] == "hyperv")
        if case == "only-hyperv":
            remove_vm(destination, lambda _, target: target["backend"] != "hyperv")
            hosts = {key: value for key, value in initial["hosts"].items() if value["backend"] == "hyperv"}
            (destination / "hosts.local.nix").write_text(nix(hosts) + "\n")
        if case == "renamed":
            hosts = {"renamed-" + key: value for key, value in initial["hosts"].items()}
            (destination / "hosts.local.nix").write_text(nix(hosts) + "\n")
            for name, instance in raw.items():
                old = destination / "instances" / (name + ".nix")
                old.unlink()
                new = dict(instance)
                if "host" in instance:
                    new["host"] = "renamed-" + instance["host"]
                old.with_name("renamed-" + name + ".nix").write_text(nix(new) + "\n")
            for path in (destination / "management/rpi/targets").glob("*.nix"):
                if path.name != "default.nix":
                    path.rename(path.with_name("renamed-" + path.name))
        if case == "two-hyperv":
            hosts = dict(initial["hosts"])
            hosts["another-windows"] = {
                "backend": "hyperv", "connection": {
                    "sshHost": "192.168.79.125", "sshUser": "test-admin", "windowsComputerName": "OTHER",
                    "storageDirectory": "D:\\nixberry", "switchName": "Other Switch",
                    "switchId": "c08cb7b8-9b3c-408e-8e30-5e16a3aeb445"}}
            (destination / "hosts.local.nix").write_text(nix(hosts) + "\n")
            target = {"host": "another-windows", "spec": {
                "name": "nixberry-another-hv", "ownerId": "nixberry-hyperv-85a5abbf-ac9f-44ad-9f34-34dd14b77123",
                "generation": 2, "secureBoot": False, "mac": "027981000055",
                "memoryMiB": 6144, "vcpus": 2, "diskGiB": 48}}
            (destination / "instances/another-hv.nix").write_text(nix(target) + "\n")
            if not any(item["backend"] == "hyperv" for item in initial["instances"].values()):
                # Regrese musí mít dvě VM i po odebrání všech původních deklarací.
                control = {**target, "spec": {**target["spec"], "name": "nixberry-control-hv",
                    "ownerId": "nixberry-hyperv-95a5abbf-ac9f-44ad-9f34-34dd14b77123",
                    "mac": "027981000056", "diskGiB": 32}}
                (destination / "instances/control-hv.nix").write_text(nix(control) + "\n")
        if case == "portable":
            hosts = evaluate("import " + str(destination / "management/examples/hosts.nix"))
            (destination / "hosts.local.nix").write_text(nix(hosts) + "\n")
            remove_vm(destination, lambda *_: True)
            remove_rpi(destination)
            for index, (host, description) in enumerate(sorted(hosts.items()), start=1):
                backend = description["backend"]
                identity = f"12345678-1234-4321-8123-{index:012d}"
                target = {"host": host, "spec": {
                    "name": f"nixberry-portable-{index}", "uuid": identity, "ownerId": "nixberry-" + identity,
                    "mac": f"00:50:56:12:34:{index:02x}", "memoryMiB": 4096, "vcpus": 2, "diskGiB": 32}}
                if backend == "proxmox":
                    target["spec"]["vmid"] = 9000 + index
                (destination / f"instances/portable-{index}.nix").write_text(nix(target) + "\n")
        state = summary(destination)
        expected = registry(destination)
        assert sorted(state["manifest"]) == sorted(expected["targets"]), case
        assert sorted(state["systems"]) == sorted(state["images"]) == sorted(state["targets"]), case
        for name, target in expected["instances"].items():
            assert state["systems"][name]["hostname"] == target["guestHostName"], (case, name)
            if target.get("offline"):
                assert name not in state["vmTools"], (case, name)
                assert state["manifest"][name]["commands"] == {}, (case, name)
                if target["capabilities"]["system"]:
                    assert "update" in state["manifest"][name]["operations"], (case, name)
                    assert "reproduce" not in state["manifest"][name]["operations"], (case, name)
                else:
                    assert state["manifest"][name]["operations"] == ["show", "export"], (case, name)
            else:
                assert state["vmTools"][name]["systemPath"] == state["systems"][name]["path"], (case, name)
                assert state["vmTools"][name]["diskGiB"] == target["spec"]["diskGiB"], (case, name)
                assert "provision" in state["manifest"][name]["commands"], (case, name)
        if case == "empty-all":
            assert state["targets"] == state["hosts"] == state["recoveryTargets"] == []
            assert state["vmTools"] == state["images"] == {}
        if case == "two-hyperv":
            other = state["vmTools"]["another-hv"]
            assert other["sshHost"] == "192.168.79.125" and other["diskGiB"] == 48
            assert state["systems"]["another-hv"]["hostname"] == "nixberry-another-hv"
            for name, target in expected["instances"].items():
                if name != "another-hv" and target["backend"] == "hyperv":
                    assert other["isoPath"] != state["vmTools"][name]["isoPath"]
                    assert state["images"][name] != state["images"]["another-hv"]
        if case == "portable":
            for name, target in expected["instances"].items():
                tool = state["vmTools"][name]
                assert tool["target"]["backend"] == target["backend"]
                assert tool["target"]["spec"] == target["spec"]
                assert tool["imagePath"] and set(state["manifest"][name]["commands"]) == {"probe", "create", "install", "provision"}
                assert {"doctor", "cleanup"} <= set(state["manifest"][name]["operations"])
        records[case] = state
        (root / (case + ".json")).write_text(json.dumps(state, indent=2) + "\n")
        info(_('OK {case}: {value2}'), case=case, value2=', '.join(state['targets']))

    destination = copy("missing-host")
    (destination / "hosts.local.nix").write_text("{}\n")
    (destination / "instances/orphan.nix").write_text(
        '{ host = "missing"; spec.name = "nixberry-orphan"; }\n')
    try:
        registry(destination)
    except RuntimeError as error:
        assert "hostitel" in str(error) and "není deklarovaný" in str(error)
    else:
        raise AssertionError(_('Osiřelá deklarace hostitele nebyla odmítnutá.'))
    info(_('OK missing-host: srozumitelná chyba konfigurace'))

    # Skutečně sestavíme prázdný management, nejen líně vyhodnotíme jeho atributy.
    subprocess.run(["nix", "build", "--no-link", "--no-write-lock-file",
                    "path:" + str(root / "empty-all") + "#nixberry"], check=True)
    for action in ("list", "status"):
        result = subprocess.run([records["empty-all"]["tool"] + "/bin/nixberry", action],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
    info(_('OK empty CLI: build, list a status bez hostitelů a cílů'))
    info(_('Všechny zkoušky evidence prošly; VM ani hostitelé nebyli kontaktovaní.'))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
