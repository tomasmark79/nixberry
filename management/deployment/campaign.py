#!/usr/bin/env python3
"""Sekvenční průřezová kampaň NixBerry podle docs/test-plan.md."""

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from messages import _, ArgumentParser, info, error, python_script
from portable import rpc, transport, wait_stopped
from runtime import status as live_status


HOSTS = [
    ("lv-lx", "libvirt", "local-libvirt"),
    ("hv-win", "hyperv", "musicbox-hyperv"),
    ("pve-lx", "proxmox", "pvelab-proxmox"),
    ("vbox-lx", "virtualbox", "nixon-virtualbox"),
    ("vbox-win", "virtualbox", "musicbox-virtualbox"),
    ("vmw-lx", "vmware", "nixon-vmware"),
    ("vmw-win", "vmware", "musicbox-vmware"),
]


def matrix():
    cases = []
    for code, backend, host in HOSTS:
        for method in ("image", "iso"):
            route = ("provision" if code == "vbox-win" else "split" if method == "iso"
                     and code in ("hv-win", "vbox-lx", "vmw-win") else "reproduce")
            cases.append(dict(id=f"ON-{code}-{method}", group="ON", backend=backend,
                              host=host, method=method, route=route, vcpus=4 if code == "vbox-win" else 2))
    for code, backend in (("lv", "libvirt"), ("hv", "hyperv"), ("pve", "proxmox"),
                          ("vbox", "virtualbox"), ("vmw", "vmware")):
        for method in ("image", "iso"):
            cases.append(dict(id=f"OFF-{code}-{method}", group="OFF", backend=backend, method=method))
    for code, host in (("vmw-lx", "nixon-vmware"), ("vmw-win", "musicbox-vmware")):
        cases.append(dict(id=f"NET-{code}-static", group="NET", backend="vmware",
                          host=host, method="image", route="reproduce"))
    for code, backend, host in (("lv", "libvirt", "local-libvirt"),
                                ("vmw-win", "vmware", "musicbox-vmware")):
        for method in ("image", "iso"):
            cases.append(dict(id=f"HAND-{code}-{method}", group="HAND", backend=backend,
                              host=host, method=method))
    cases.extend(dict(id="NATIVE-" + key, group="NATIVE", target=name)
                 for key, name in (("kozlonix", "kozlonix"), ("rpi", "nixberry")))
    return cases


class Blocked(RuntimeError):
    pass


class Campaign:
    def __init__(self, recipe, root, settings=None):
        self.recipe = Path(recipe).resolve()
        self.root = Path(root).resolve()
        if self.root == self.recipe or self.recipe in self.root.parents:
            raise ValueError(_("Výsledky kampaně ukládej mimo recept."))
        self.settings = settings or {}
        self.report = None
        self.case = None
        self.folder = None
        self.work = None
        self.counter = 0

    def save(self):
        path = self.root / "report.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
        rows = ["# Výsledky kampaně NixBerry", "", "| Případ | Výsledek | Podrobnosti |",
                "| --- | --- | --- |"]
        for item in self.report["cases"]:
            detail = item.get("detail", "").replace("|", "/").replace("\n", " ")
            rows.append(f'| {item["id"]} | {item["result"]} | {detail} |')
        (self.root / "report.md").write_text("\n".join(rows) + "\n")

    def command(self, argv, *, cwd=None, expected=0, timeout=3600, input_text=None, env=None):
        self.counter += 1
        stem = self.folder / f"{self.counter:03d}"
        started = time.time()
        entry = {"argv": [str(x) for x in argv], "cwd": str(cwd or self.work or self.recipe),
                 "started": started, "stdout": str(stem.with_suffix(".stdout").relative_to(self.root)),
                 "stderr": str(stem.with_suffix(".stderr").relative_to(self.root))}
        self.case.setdefault("steps", []).append(entry)
        self.save()
        with stem.with_suffix(".stdout").open("w") as stdout, stem.with_suffix(".stderr").open("w") as stderr:
            process = subprocess.Popen(argv, cwd=cwd or self.work or self.recipe,
                                       stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, text=True, start_new_session=True, env=env)
            try:
                process.communicate(input_text, timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                entry.update(returncode=process.returncode, timeout=True, finished=time.time())
                self.save()
                raise RuntimeError(_("Časový limit kroku; stav prostředků vyžaduje kontrolu."))
        entry.update(returncode=process.returncode, finished=time.time())
        self.save()
        if expected is not None and process.returncode != expected:
            tail = stem.with_suffix(".stderr").read_text()[-1800:]
            raise RuntimeError(_("Krok skončil kódem {code}: {detail}").format(code=process.returncode, detail=tail))
        return process.returncode, stem.with_suffix(".stdout").read_text()

    def cli(self, *args, **kwargs):
        recipe = (self.work or self.recipe).resolve()
        # Izolovanou kopii nesmí přesměrovat nastavení interaktivního shellu.
        return self.command([str(recipe / "nixberry"), *args],
                            env={**os.environ, "NIXBERRY_RECIPE": str(recipe)}, **kwargs)

    def evaluate(self, attribute):
        return json.loads(self.command(["nix", "eval", "--json", "--no-write-lock-file",
                                       "path:" + str(self.work) + "#" + attribute])[1])

    def snapshot(self):
        if self.case.get("manual"):
            result = live_status(self.case["targetSpec"])
            self.case["operatorState"] = result
            self.save()
            return result
        data = json.loads(self.cli("status", self.case["target"], "--json")[1])
        return data[self.case["target"]]

    def fresh_recipe(self, *, offline=False):
        self.work = self.folder / "recipe"
        shutil.copytree(self.recipe, self.work, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "result*", ".git", ".direnv"))
        overrides = self.settings.get("hostOverrides", {})
        if overrides:
            host_file = self.work / "hosts.local.nix"
            if not host_file.exists():
                host_file.write_text("{}\n")
            host_file.rename(self.work / "hosts-base.nix")
            entries = []
            for key, value in overrides.items():
                if not re.fullmatch(r"[a-zA-Z0-9-]+", key):
                    raise ValueError(_("Neplatné jméno testovacího hostitele."))
                entries.append(key + " = original." + key + " // { connection = original." + key
                               + ".connection // " + nix_attrs(value) + "; };")
            host_file.write_text("let original = import ./hosts-base.nix; in original // { " + " ".join(entries) + " }\n")
        name = "vm-demo-test-" + self.case["id"].lower() + "-" + self.report["runId"][:4]
        if len(name) > 40:
            raise ValueError(_("Testovací jméno překračuje limit CLI."))
        self.case["target"] = name
        self.save()
        args = ["new-" + self.case["backend"] + "-vm", name]
        args += ["--offline"] if offline else ["--host", self.case["host"]]
        if self.case["method"] == "iso":
            args += ["--method", "iso"]
        self.cli(*args)
        file = self.work / "instances" / (name + ".nix")
        text = file.read_text().replace("vcpus = 4;", "vcpus = " + str(self.case.get("vcpus", 2)) + ";")
        # Identita hypervizoru zůstává jedinečná, společný hostname dovolí
        # sdílet totožné artefakty mezi sekvenčními případy stejného profilu.
        text = text.replace("{\n", '{\n  guestHostName = "nixberry-campaign";\n', 1)
        file.write_text(text)
        self.set_revision("A")
        self.case["targetSpec"] = self.evaluate("nixberryDeployment.instances." + name)
        self.save()
        return self.case["targetSpec"]

    def set_revision(self, revision):
        path = self.work / "platforms/vm/configuration.nix"
        text = path.read_text()
        text = re.sub(r'\n  environment\.etc\."nixberry-campaign"\.text = .*?;\n', "\n", text)
        head, tail = text.rsplit("}", 1)
        marker = json.dumps("nixberry-campaign " + revision + "\n")
        path.write_text(head + '  environment.etc."nixberry-campaign".text = ' + marker + ";\n}" + tail)

    def preflight(self):
        hosts = self.evaluate("nixberryDeployment.hosts")
        host = hosts[self.case["host"]]
        connection = host["connection"]
        if connection.get("transport") == "ssh" or host["backend"] == "hyperv":
            code, _out = self.command(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                                      "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
                                      connection["sshUser"] + "@" + connection["sshHost"], "hostname"],
                                     expected=None, timeout=20)
            if code:
                raise Blocked(_("Deklarovaný hostitel není dostupný přes ověřené SSH."))

    def guest(self, script, **kwargs):
        # OpenSSH spojuje argumenty vzdáleného příkazu; předáme jediný úplný
        # shellový program, aby se neztratilo uvozování sh -c.
        return self.cli("ssh", self.case["target"], "--", "set -eu; " + script, **kwargs)

    def wait_off(self):
        target = self.case.get("targetSpec", {})
        if target.get("backend") in ("proxmox", "virtualbox", "vmware"):
            wait_stopped(target)
            return
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = self.snapshot()["state"]
            if state == "shut off":
                return
            if state != "running":
                raise RuntimeError(_("VM při vypínání přešla do neočekávaného stavu."))
            time.sleep(2)
        raise RuntimeError(_("VM se korektně nevypnula; další krok je zastaven."))

    def poweroff(self):
        code, _output = self.guest("systemctl poweroff --no-block", expected=None)
        if code not in (0, 255):
            raise RuntimeError(_("Vypnutí VM se nepodařilo vyžádat."))
        # SSH může zaniknout dříve než dorazí odpověď. Rozhoduje ověřený
        # vypnutý stav vlastní VM v hypervizoru, nikoli samotný kód 255.
        self.wait_off()

    def powershell(self, body):
        spec = self.case["targetSpec"]["spec"]
        data = base64.b64encode(json.dumps({**spec, "campaignVMId": self.case.get("nativeVMId")}).encode()).decode()
        script = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';\n"
                  "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + data + "'))|ConvertFrom-Json;\n"
                  "if($env:COMPUTERNAME -ne $s.windowsComputerName){throw 'Hostitel nesouhlasí'};\n"
                  "$v=Get-VM -Name $s.name -ErrorAction Stop;\n"
                  "if($v.Notes -ne $s.ownerId){throw 'Vlastník nesouhlasí'};\n"
                  "if($s.campaignVMId -and $v.Id.ToString() -ne $s.campaignVMId){throw 'VM ID nesouhlasí'};\n" + body)
        encoded = base64.b64encode(script.encode("utf-16le")).decode()
        return self.command(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             spec["sshUser"] + "@" + spec["sshHost"],
                             "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded], timeout=120)

    def start(self):
        target = self.case["targetSpec"]
        spec = target["spec"]
        if target["backend"] == "libvirt":
            self.command(["virsh", "-c", spec["uri"], "start", spec["name"]])
        elif target["backend"] == "hyperv":
            self.powershell("if($v.State -ne 'Off'){throw 'VM není vypnutá'}; Start-VM -VM $v")
        else:
            rpc(target, "start")

    def checks(self):
        name = self.case["target"]
        commands = [("status", name, "--json"), ("verify", name, "--json"), ("doctor", name, "--json")]
        if not self.case.get("manual"):
            commands.insert(1, ("probe", name))
        for args in commands:
            self.cli(*args)
        self.guest("test -d /sys/firmware/efi; ! mountpoint -q /iso; ip -4 route; "
                   "getent hosts cache.nixos.org; curl -fsS --max-time 20 https://cache.nixos.org/nix-cache-info")
        if self.case["group"] == "NET":
            expected = self.settings["networks"][self.case["host"]]
            addresses = json.loads(self.guest("ip -j -4 address show scope global")[1])
            routes = json.loads(self.guest("ip -j -4 route show default")[1])
            dns = self.guest("if systemctl is-active --quiet systemd-resolved; then resolvectl dns; "
                             "else cat /etc/resolv.conf; fi")[1]
            validate_static_network(expected, addresses, routes, dns)
            self.case["networkEvidence"] = dict(addresses=addresses, routes=routes, dns=dns)
            self.save()

    def update_cycle(self):
        name = self.case["target"]
        self.guest("printf '%s\\n' nixberry-campaign-data > /root/nixberry-campaign-data; "
                   "sha256sum /root/nixberry-campaign-data > /root/nixberry-campaign-data.sha256")
        system_a = self.guest("readlink -f /run/current-system")[1].strip()
        self.set_revision("B")
        self.cli("diff", name)
        self.cli("update", name)
        if self.guest("readlink -f /run/current-system")[1].strip() != system_a:
            raise RuntimeError(_("Náhled aktualizace změnil běžící systém."))
        self.cli("update", name, "--apply")
        system_b = self.guest("readlink -f /run/current-system")[1].strip()
        if system_a == system_b:
            raise RuntimeError(_("Aktualizace nezměnila systémovou generaci."))
        self.guest("sha256sum -c /root/nixberry-campaign-data.sha256; test -f /etc/nixberry-campaign")
        self.cli("verify", name, "--json")
        self.cli("diff", name)
        self.cli("diffg", name)
        # Předaný offline recept zná přístup do systému, nikoli hostitele
        # hypervizoru. Přehled jeho prostředků proto správně odmítne.
        self.cli("cleanup", name, "--json", expected=2 if self.case.get("manual") else 0)
        self.case.update(systemA=system_a, systemB=system_b)
        self.save()

    def online(self):
        target = self.fresh_recipe()
        self.preflight()
        if self.case["group"] == "NET":
            network = self.settings.get("networks", {}).get(self.case["host"])
            if not network:
                raise Blocked(_("Chybí vyhrazená statická adresa testovací NAT sítě."))
            file = self.work / "instances" / (self.case["target"] + ".nix")
            text, count = re.subn(r'network = \{.*?\n  \};', "network = " + nix_attrs(network) + ";", file.read_text(), flags=re.S)
            if count != 1 or network.get("mode") != "static":
                raise RuntimeError(_("Test pevné sítě nezměnil právě jednu deklaraci na statickou konfiguraci."))
            file.write_text(text)
            configured = self.evaluate("nixberrySystems." + self.case["target"] + ".config.nixberry.vm.network")
            if configured != network:
                raise RuntimeError(_("Vyhodnocená síť testovací VM neodpovídá zadaným hodnotám."))
            target = self.evaluate("nixberryDeployment.instances." + self.case["target"])
            self.case["targetSpec"] = target
        before = self.snapshot()
        if before["state"] != "absent":
            raise RuntimeError(_("Nový testovací cíl už existuje; nic nepřebírám."))
        self.cli("show", self.case["target"], "--json")
        self.cli("plan", self.case["target"])
        if self.snapshot() != before:
            raise RuntimeError(_("Plán změnil stav hypervizoru."))
        self.case["writeStarted"] = True
        self.save()
        if self.case["route"] == "split":
            self.cli("create", self.case["target"], "--apply")
            middle = self.snapshot()["state"]
            if middle != ("running" if self.case["method"] == "iso" else "shut off"):
                raise RuntimeError(_("Stav po create neodpovídá metodě."))
            self.cli("install", self.case["target"], "--apply")
        else:
            self.cli(self.case["route"], self.case["target"], "--apply")
        if target["backend"] == "hyperv":
            self.case["nativeVMId"] = self.powershell("$v.Id.ToString()")[1].strip()
            self.save()
        self.checks()
        self.update_cycle()
        self.poweroff()
        self.start()
        self.wait_verified()
        self.set_revision("A")
        code, _ = self.cli("create", self.case["target"], "--apply", expected=None)
        self.set_revision("B")
        if code == 0:
            raise RuntimeError(_("Opakované vytvoření bylo nesprávně povoleno."))
        self.guest("sha256sum -c /root/nixberry-campaign-data.sha256")
        self.cleanup()

    def wait_verified(self):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            code, _ = self.cli("verify", self.case["target"], "--json", expected=None)
            if code == 0:
                self.checks()
                return
            time.sleep(3)
        raise RuntimeError(_("Systém po novém startu neprošel ověřením."))

    def offline(self):
        self.fresh_recipe(offline=True)
        name = self.case["target"]
        file = self.work / "instances" / (name + ".nix")
        opposite = "iso" if self.case["method"] == "image" else "image"
        file.write_text(file.read_text().replace('deploymentMethod = "' + self.case["method"] + '";',
                                                'deploymentMethod = "' + opposite + '";'))
        self.case["declarationMethod"] = opposite
        bundle = self.folder / "bundle"
        self.cli("export", name, "--method", self.case["method"], "--output", str(bundle), "--dry-run")
        if bundle.exists():
            raise RuntimeError(_("Náhled exportu zapsal balíček."))
        elsewhere = self.folder / "elsewhere"
        elsewhere.mkdir()
        self.cli("export", name, "--method", self.case["method"], "--output", "../bundle", cwd=elsewhere)
        self.command(["sha256sum", "-c", "SHA256SUMS"], cwd=bundle)
        manifest = json.loads((bundle / "manifest.json").read_text())
        if manifest["deploymentMethod"] != self.case["method"] or manifest["backend"] != self.case["backend"]:
            raise RuntimeError(_("Export neodpovídá zvolené platformě a metodě."))
        lock = hashlib.sha256((bundle / "recipe/flake.lock").read_bytes()).hexdigest()
        if lock != manifest["lockSha256"]:
            raise RuntimeError(_("Exportovaný lock neodpovídá manifestu."))
        artifact = bundle / manifest["artifact"]
        if self.case["method"] == "image":
            details = json.loads(self.command(["qemu-img", "info", "--output=json", str(artifact)])[1])
            expected = {"libvirt": "qcow2", "hyperv": "vhdx", "proxmox": "qcow2", "virtualbox": "vdi", "vmware": "vmdk"}
            if details["format"] != expected[self.case["backend"]] or details["virtual-size"] != 32 * 1024**3:
                raise RuntimeError(_("Exportovaný disk má nesprávný formát nebo kapacitu."))
        else:
            if not (bundle / "INSTALL-ISO.sh").is_file() or not manifest.get("diskoPath"):
                raise RuntimeError(_("ISO balíček neobsahuje instalační postup."))
        damaged = self.folder / "damaged"
        damaged.mkdir()
        copy = damaged / artifact.name
        self.command(["cp", "--reflink=auto", str(artifact), str(copy)])
        with copy.open("r+b") as stream:
            first = stream.read(1)
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
        checksum = next(line for line in (bundle / "SHA256SUMS").read_text().splitlines()
                        if line.endswith("  " + artifact.name))
        (damaged / "SHA256SUMS").write_text(checksum + "\n")
        if self.command(["sha256sum", "-c", "SHA256SUMS"], cwd=damaged, expected=None)[0] == 0:
            raise RuntimeError(_("Kontrolní součet nerozpoznal poškozenou kopii."))
        copy.unlink()
        if self.cli("export", name, "--output", str(bundle), expected=None)[0] == 0:
            raise RuntimeError(_("Export přepsal existující balíček."))
        self.case["bundle"] = str(bundle)
        self.save()

    def native(self):
        self.work = self.recipe
        name = self.case["target"]
        target = self.evaluate("nixberryDeployment.targets." + name)
        self.cli("probe", name, expected=2 if target.get("offline") and target["backend"] != "rpi" else 0)
        findings = []
        for args in (("show", name, "--json"), ("status", name, "--json"),
                     ("ssh", name, "--", "uname", "-a"), ("doctor", name, "--json"),
                     ("verify", name, "--json")):
            code, _output = self.cli(*args, expected=None)
            if code:
                findings.append(dict(command=args[0], returncode=code))
        self.case["nativeFindings"] = findings
        self.save()
        if findings:
            raise RuntimeError(_("Provozní cíl má nálezy: {checks}. Kontroly byly pouze pro čtení.").format(
                checks=", ".join(item["command"] + "=" + str(item["returncode"]) for item in findings)))

    def cleanup(self):
        if self.snapshot()["state"] != "shut off":
            self.poweroff()
        target = self.case["targetSpec"]
        info(_("Úklid vlastního testovacího cíle: {name}"), name=self.case["target"])
        if not self.case.get("manual") or target["backend"] == "libvirt":
            file = self.work / "instances" / (self.case["target"] + ".nix")
            text = file.read_text()
            if "allowReset = false;" not in text:
                raise RuntimeError(_("Testovací deklarace nemá očekávané výchozí oprávnění; úklid zastavuji."))
            text = text.replace("allowReset = false;", "allowReset = true;")
            if self.case.get("manual"):
                text = re.sub(r'\n  access = \{[^\n]*\};', "", text)
                text = text.replace("{\n", '{\n  host = "local-libvirt";\n', 1)
            file.write_text(text)
            self.cli("reset", self.case["target"])
            self.cli("reset", self.case["target"], "--apply")
            for media in self.case.get("operatorMedia", []) if target["backend"] == "libvirt" else []:
                self.command(["virsh", "-c", target["spec"]["uri"], "vol-delete", media,
                              "--pool", target["spec"]["storagePool"]])
        elif target["backend"] == "hyperv":
            self.powershell("""
if($v.State -ne 'Off'){throw 'VM není vypnutá'}
$root=[IO.Path]::GetFullPath((Join-Path $s.storageDirectory $s.name)).TrimEnd('\\')
if(-not $s.name.StartsWith('nixberry-vm-demo-test-')){throw 'Cizí jméno'}
if(-not ([IO.Path]::GetFullPath($v.Path)).StartsWith($root+'\\')){throw 'Cizí cesta'}
if(@(Get-VMSnapshot -VM $v).Count){throw 'Snapshoty'}
$disks=@(Get-VMHardDiskDrive -VM $v)
if($disks.Count -ne 1 -or -not ([IO.Path]::GetFullPath($disks[0].Path)).StartsWith($root+'\\')){throw 'Cizí disk'}
if((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Odkaz'}
if(@(Get-ChildItem -LiteralPath $root -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count){throw 'Odkaz uvnitř adresáře'}
Remove-VM -VM $v -Force
Remove-Item -LiteralPath $root -Recurse -Force
""")
        else:
            source = python_script(Path(__file__).with_name("host_agent.py")) + "\n"
            source += python_script(Path(__file__).with_name("campaign_host.py")) + "\n"
            request = json.dumps({"spec": target["spec"], "backend": target["backend"], "action": "cleanup"})
            source += "print(json.dumps(campaign_action(json.loads(" + repr(request) + "))))\n"
            command = transport(target["spec"])
            bootstrap = "import sys;exec(sys.stdin.buffer.read(" + str(len(source.encode("utf-8"))) + "))"
            if target["spec"]["transport"] == "ssh":
                interpreter = command.pop()
                command.append(interpreter + ' -c "' + bootstrap + '"')
            else:
                command += ["-c", bootstrap]
            self.command(command, input_text=source, timeout=180)
        if self.snapshot()["state"] != "absent":
            raise RuntimeError(_("Po úklidu cíl není nepřítomný."))
        self.case["cleaned"] = True
        self.save()

    def run(self, selected, *, resume=False):
        self.root.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.root.with_name(self.root.name + ".lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(_("Tuto kampaň už provádí jiný proces.")) from None
            return self._run(selected, resume=resume)

    def _run(self, selected, *, resume=False):
        if resume:
            self.report = json.loads((self.root / "report.json").read_text())
        else:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
            self.report = dict(runId=uuid.uuid4().hex, recipe=str(self.recipe), started=time.time(),
                               cases=[{**c, "result": "NOT_RUN"} for c in matrix()])
        self.save()
        for case in self.report["cases"]:
            if selected and case["id"] not in selected or case["result"] == "PASS":
                continue
            if case.get("writeStarted") and not case.get("cleaned"):
                raise RuntimeError(_("Případ má zachované prostředky; nejprve je nutná kontrola stavu."))
            self.case = case
            case.setdefault("history", []).append({"result": case["result"], "detail": case.get("detail", ""),
                "stepCount": len(case.get("steps", [])), "targetSpec": case.get("targetSpec"),
                "directory": case.get("directory"), "writeStarted": case.get("writeStarted"),
                "cleaned": case.get("cleaned"), "nativeVMId": case.get("nativeVMId")})
            # Uchováme i adresář vzniklý těsně před přerušením prvního zápisu
            # reportu. Nikdy jej nepoužijeme znovu ani nepřepíšeme jeho logy.
            while True:
                case["attempt"] = case.get("attempt", 0) + 1
                self.folder = self.root / case["id"] / ("attempt-" + str(case["attempt"]))
                try:
                    self.folder.mkdir(parents=True)
                    break
                except FileExistsError:
                    continue
            for key in ("writeStarted", "cleaned", "targetSpec", "nativeVMId", "manual", "bundle",
                        "operatorMedia", "operatorManifest", "operatorState", "systemA", "systemB", "finished", "detail"):
                case.pop(key, None)
            case["result"] = "RUNNING"
            case["directory"] = str(self.folder)
            self.work = None
            self.counter = len(case.get("steps", []))
            self.save()
            info(_("Případ kampaně: {name}"), name=case["id"])
            try:
                if case["group"] in ("ON", "NET"):
                    self.online()
                elif case["group"] == "OFF":
                    self.offline()
                elif case["group"] == "NATIVE":
                    self.native()
                else:
                    self.handoff()
                case.update(result="PASS", finished=time.time())
            except Blocked as failure:
                case.update(result="BLOCKED", detail=str(failure), finished=time.time())
            except Exception as failure:
                case.update(result="FAIL", detail=str(failure), finished=time.time())
                self.save()
                if case["group"] != "NATIVE":
                    raise
            self.save()
        return int(any(c["result"] != "PASS" for c in self.report["cases"]))

    def handoff(self):
        code = "lv" if self.case["backend"] == "libvirt" else "vmw"
        dependency = "OFF-" + code + "-" + self.case["method"]
        exported = next(c for c in self.report["cases"] if c["id"] == dependency)
        if exported["result"] != "PASS":
            raise Blocked(_("Nejprve musí projít odpovídající offline export."))
        bundle = Path(exported["bundle"])
        self.command(["sha256sum", "-c", "SHA256SUMS"], cwd=bundle)
        manifest = json.loads((bundle / "manifest.json").read_text())
        self.work = self.folder / "recipe"
        shutil.copytree(bundle / "recipe", self.work, symlinks=True)
        # Export zachovává zdroje z Nix store jen pro čtení. Zapisujeme
        # pouze připojení a testovací revizi v oddělené pracovní kopii.
        for path in (self.work / "instances" / (manifest["target"] + ".nix"),
                     self.work / "platforms/vm/configuration.nix"):
            if path.is_symlink():
                raise RuntimeError(_("Pracovní deklarace předaného balíčku nesmí být symbolický odkaz."))
            path.chmod(path.stat().st_mode | 0o200)
        self.case["target"] = manifest["target"]
        hosts = self.evaluate("nixberryDeployment.hosts")
        spec = {**manifest["spec"], **hosts[self.case["host"]]["connection"]}
        if self.case["backend"] == "libvirt":
            spec.update(volume=spec["name"] + ".qcow2", allowReset=False)
        target = dict(backend=self.case["backend"], deploymentMethod=self.case["method"], spec=spec,
                      guestHostName=manifest["guestHostName"], offline=False, host=self.case["host"])
        self.case.update(targetSpec=target, manual=True)
        self.save()
        self.preflight()
        if self.snapshot()["state"] != "absent":
            raise RuntimeError(_("Cíl pro předání už existuje; import se nepovoluje."))
        self.case["writeStarted"] = True
        self.save()
        artifact = bundle / manifest["artifact"]
        if self.case["backend"] == "libvirt":
            self.import_libvirt(manifest, artifact)
        else:
            # Importér operátora pracuje výhradně s předaným médiem. Použije
            # nativní hostitelský adaptér z balíčku, systém znovu nesestavuje.
            media = self.folder / "operator-media"
            media.mkdir()
            (media / "system.vmdk").symlink_to(artifact)
            operation = {**manifest, "target": target, "imagePath": str(media), "isoPath": str(artifact)}
            program = ("import sys,json;sys.path.insert(0," + repr(str(bundle / "recipe/management/deployment")) + ");"
                       "from portable import create,install_iso,start_and_verify;"
                       "m=json.loads(" + repr(json.dumps(operation)) + ");create(m['target'],m);"
                       + ("install_iso(m['target'],m,apply=True)" if self.case["method"] == "iso"
                          else "start_and_verify(m['target'],m)"))
            self.command([sys.executable, "-c", program])
        try:
            self.connect_offline()
            self.checks()
            self.update_cycle()
            self.cleanup()
        finally:
            forward = getattr(self, "forward", None)
            if forward is not None:
                forward.terminate()
                forward.wait(timeout=10)
                self.forward = None

    def import_libvirt(self, manifest, artifact):
        spec = self.case["targetSpec"]["spec"]
        prefix = ["virsh", "-c", spec["uri"]]
        pool = spec["storagePool"]
        disk = spec["storageDirectory"] + "/" + spec["volume"]
        if self.command([*prefix, "vol-info", spec["volume"], "--pool", pool], expected=None)[0] == 0:
            raise RuntimeError(_("Import by přepsal existující svazek."))
        self.command([*prefix, "vol-create-as", pool, spec["volume"], str(spec["diskGiB"]) + "G", "--format", "qcow2"])
        if self.command([*prefix, "vol-path", spec["volume"], "--pool", pool])[1].strip() != disk:
            raise RuntimeError(_("Import vytvořil svazek na jiné cestě."))
        boot = "uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"
        args = ["virt-install", "--connect", spec["uri"], "--name", spec["name"], "--uuid", spec["uuid"],
                "--memory", str(spec["memoryMiB"]), "--vcpus", str(spec["vcpus"]), "--osinfo", "generic",
                "--machine", spec["machine"], "--boot", boot, "--graphics", "spice",
                "--network", "network=" + spec["network"] + ",model=virtio,mac=" + spec["mac"],
                "--channel", "unix,target.type=virtio,target.name=org.qemu.guest_agent.0",
                "--disk", "path=" + disk + ",format=qcow2,bus=virtio", "--noautoconsole", "--print-xml=1"]
        if self.case["method"] == "image":
            self.command([*prefix, "vol-upload", spec["volume"], str(artifact), "--pool", pool])
            args += ["--import"]
        else:
            media = spec["name"] + ".iso"
            self.command([*prefix, "vol-create-as", pool, media, str(artifact.stat().st_size), "--format", "raw"])
            self.command([*prefix, "vol-upload", media, str(artifact), "--pool", pool])
            self.case["operatorMedia"] = [media]
            self.save()
            args += ["--cdrom", spec["storageDirectory"] + "/" + media]
        xml = ET.fromstring(self.command(args)[1])
        metadata = xml.find("metadata")
        if metadata is None:
            metadata = ET.SubElement(xml, "metadata")
        ET.SubElement(metadata, "{urn:nixberry:deployment}owner", {"id": spec["ownerId"]})
        file = self.folder / "operator-domain.xml"
        file.write_text(ET.tostring(xml, encoding="unicode"))
        self.command([*prefix, "define", str(file)])
        self.start()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            state = self.snapshot()
            if state.get("addresses"):
                break
            time.sleep(3)
        else:
            raise RuntimeError(_("Ručně vytvořená VM nezískala adresu."))
        if self.case["method"] == "iso":
            from runtime import ssh_command
            target = {**self.case["targetSpec"], "_acceptNewGuestKey": True}
            connection = ssh_command(target, state)
            # Klíč instalátoru patří k tomuto pokusu, nikoli do trvalé
            # evidence klíčů hotového systému. Rodič adresáře už existuje.
            installer_known = self.folder / "installer.known_hosts"
            installer_known.touch(mode=0o600, exist_ok=False)
            connection = ["UserKnownHostsFile=" + json.dumps(str(installer_known))
                          if argument.startswith("UserKnownHostsFile=") else argument for argument in connection]
            self.wait_guest_ssh(connection + ["true"])
            values = [target["spec"]["uuid"], manifest["systemPath"], manifest["diskoPath"],
                      str(spec["diskGiB"] * 1024**3), manifest["profile"], "/dev/vda"]
            script = (artifact.parent / "INSTALL-ISO.sh").read_text()
            for mode in ("dry-run", "apply"):
                self.command(connection + [shlex.join(["bash", "-s", "--", mode, *values])], input_text=script)
            code, _output = self.command(connection + ["systemctl poweroff --no-block"], expected=None)
            if code not in (0, 255):
                raise RuntimeError(_("Vypnutí VM se nepodařilo vyžádat."))
            self.wait_off()
            config = ET.fromstring(self.command([*prefix, "dumpxml", spec["name"], "--inactive"])[1])
            for cdrom in config.findall("./devices/disk[@device='cdrom']"):
                source = cdrom.find("source")
                if source is not None:
                    cdrom.remove(source)
            os_node = config.find("os")
            for node in list(os_node.findall("boot")):
                os_node.remove(node)
            ET.SubElement(os_node, "boot", {"dev": "hd"})
            # Instalační fáze virt-install vypíná VM při rebootu; hotový
            # systém má po odpojení ISO opět běžné restartování.
            reboot = config.find("on_reboot")
            if reboot is None:
                reboot = ET.SubElement(config, "on_reboot")
            reboot.text = "restart"
            file.write_text(ET.tostring(config, encoding="unicode"))
            self.command([*prefix, "define", str(file)])
            self.start()
        self.case["operatorManifest"] = manifest
        self.save()

    def connect_offline(self):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            snapshot = self.snapshot()
            if len(snapshot.get("addresses", [])) == 1:
                address = snapshot["addresses"][0]
                break
            time.sleep(3)
        else:
            raise RuntimeError(_("Předaná VM nemá ověřenou adresu."))
        port = 22
        host = address
        spec = self.case["targetSpec"]["spec"]
        if spec.get("hostPlatform") == "windows":
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            command = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes",
                       "-N", "-L", f"127.0.0.1:{port}:{address}:22", spec["sshUser"] + "@" + spec["sshHost"]]
            self.forward = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=(self.folder / "forward.stderr").open("w"))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.forward.poll() is not None:
                    raise RuntimeError(_("SSH forward se nepodařilo připravit."))
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError(_("SSH forward nezačal naslouchat."))
            host = "127.0.0.1"
        known = self.folder / "known_hosts"
        alias = self.case["id"]
        self.wait_guest_ssh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                             "-o", "StrictHostKeyChecking=accept-new",
                             "-o", "UserKnownHostsFile=" + str(known), "-o", "HostKeyAlias=" + alias,
                             "-p", str(port), "root@" + host, "hostname"])
        access = {"host": host, "user": "root", "port": str(port),
                  "knownHostsFile": str(known), "hostKeyAlias": alias}
        # Port je Nix integer, ostatní hodnoty řetězce; schéma access jej kontroluje.
        expression = nix_attrs(access).replace('port = "' + str(port) + '";', 'port = ' + str(port) + ';')
        file = self.work / "instances" / (self.case["target"] + ".nix")
        file.write_text(file.read_text().replace("{\n", "{\n  access = " + expression + ";\n", 1))
        self.wait_verified()

    def wait_guest_ssh(self, command):
        # DHCP může přidělit adresu dříve, než systém spustí sshd.
        # Připnutý klíč se při opakování nemění ani neodstraňuje.
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.command(command, expected=None, timeout=20)[0] == 0:
                return
            time.sleep(3)
        raise RuntimeError(_("Předaná VM nespustila ověřené SSH v časovém limitu."))


def validate_static_network(expected, addresses, routes, dns):
    interfaces = {link["ifname"] for link in addresses for address in link.get("addr_info", [])
                  if str(address.get("local")) + "/" + str(address.get("prefixlen")) == expected["address"]}
    if not interfaces or not any(route.get("gateway") == expected["gateway"] and route.get("dev") in interfaces
                                 for route in routes):
        raise RuntimeError(_("Běžící VM nemá očekávanou pevnou adresu, prefix nebo výchozí bránu."))
    servers = set(re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", dns))
    if not set(expected["dns"]).issubset(servers):
        raise RuntimeError(_("Běžící VM nepoužívá očekávané DNS servery."))


def nix_attrs(value):
    if isinstance(value, dict):
        return "{ " + " ".join(str(k) + " = " + nix_attrs(v) + ";" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[ " + " ".join(nix_attrs(v) for v in value) + " ]"
    if isinstance(value, str):
        return json.dumps(value).replace("${", "\\${")
    raise ValueError(_("Nepodporovaná hodnota testovací konfigurace."))


def main():
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument("--recipe", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--case", action="append", choices=[c["id"] for c in matrix()], default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps(matrix(), ensure_ascii=False, indent=2))
        return 0
    if not args.run_root:
        parser.error(_("Pro běh kampaně zadej --run-root mimo recept."))
    settings = json.loads(args.settings.read_text()) if args.settings else {}
    return Campaign(args.recipe, args.run_root, settings).run(args.case, resume=args.resume)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
        error("{detail}", detail=failure)
        raise SystemExit(1)
