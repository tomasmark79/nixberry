"""Živý stav a přístup do RPi i VM; ověřená volba SSH cíle."""

from messages import _, forward_stderr

import ipaddress
import json
import os
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET


def read(command):
    result = subprocess.run(command, text=True, capture_output=True, timeout=75,
                            env={**os.environ, "LC_ALL": "C"}, check=False)
    if result.returncode:
        # SSH může mít na stderr pouze upozornění, zatímco skutečná příčina je na stdout.
        raise RuntimeError("\n".join(part for part in (result.stderr.strip(), result.stdout.strip()) if part)
                           or _('Hostitel neodpověděl.'))
    forward_stderr(result.stderr)
    return result.stdout.strip()


def mac(value):
    return value.replace(":", "").replace("-", "").lower()


def addresses(values, excluded=()):
    networks = [ipaddress.ip_network(value) for value in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    result = set()
    for value in values:
        try:
            address = ipaddress.ip_interface(value).ip
        except ValueError:
            continue
        if address.version == 4 and any(address in net for net in networks):
            if str(address) not in {"192.168.79.1", *excluded}:
                result.add(str(address))
    return sorted(result)


def guest_connection(value):
    """Výslovný přístup přímo do VM; nevybírá ani nekonfiguruje hypervizor."""
    match = re.fullmatch(r"root@([A-Za-z0-9][A-Za-z0-9.-]*)(?::([0-9]{1,5}))?", value)
    if not match:
        raise ValueError(_('Přímé SSH zadej jako root@HOST[:PORT].'))
    host, port = match.group(1), int(match.group(2) or 22)
    if not 1 <= port <= 65535 or len(host) > 253:
        raise ValueError(_('Neplatný SSH hostitel nebo port (1–65535).'))
    if re.fullmatch(r"[0-9.]+", host):
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError as error:
            raise ValueError(_('Neplatná IPv4 adresa SSH cíle.')) from error
        if address.is_unspecified or address.is_multicast:
            raise ValueError(_('SSH vyžaduje adresu konkrétního hostitele.'))
    elif not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                 for label in host.split(".")):
        raise ValueError(_('Neplatné DNS jméno SSH cíle.'))
    return {"host": host, "port": port, "user": "root"}


def guest_host_option(target):
    direct = target.get("_guestConnection")
    return (" --ssh " + shlex.quote(f'{direct["user"]}@{direct["host"]}:{direct["port"]}')
            if direct else "")


def validate_access(value):
    """Normalizace deklarovaného SSH bez změny důvěry v klíč serveru."""
    keys = {"host", "port", "user", "knownHostsFile", "hostKeyAlias"}
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError(_('access přijímá pouze host, port, user, knownHostsFile a hostKeyAlias.'))
    host, port, user = value.get("host"), value.get("port", 22), value.get("user", "root")
    if not isinstance(host, str) or type(port) is not int or user != "root":
        raise ValueError(_('access vyžaduje host, celočíselný port a účet root.'))
    result = guest_connection(f"{user}@{host}:{port}")
    known = value.get("knownHostsFile")
    if known is not None:
        if not isinstance(known, str) or not os.path.isabs(known) or any(ord(c) < 32 for c in known):
            raise ValueError(_('knownHostsFile musí být absolutní cesta bez řídicích znaků.'))
        result["knownHostsFile"] = known
    alias = value.get("hostKeyAlias")
    if alias is not None:
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", alias):
            raise ValueError(_('Neplatný hostKeyAlias pro ověřování SSH klíče.'))
        result["hostKeyAlias"] = alias
    return result


def direct_connection(target):
    declared = target.get("access")
    if declared is not None and (target.get("backend") not in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware") or not target.get("offline")):
        raise ValueError(_('access podporuje offline VM bez správy hypervizoru.'))
    if declared is not None:
        declared = validate_access(declared)
    override = target.get("_guestConnection")
    if override:
        # Změna adresy nesmí zahodit deklarovaný soubor klíčů ani stabilní alias.
        return {**(declared or {}), **validate_access(override)}
    return declared


def capabilities(target):
    managed = not target.get("offline", False)
    direct = direct_connection(target)
    return {"build": True, "export": True,
            "system": managed or bool(direct),
            "hypervisor": managed and target.get("backend") != "rpi"}


def hyperv_status(target, data):
    """Ověření identity VM nad jedním čerstvým přehledem hostitele."""
    spec = target["spec"]
    if data["computerName"].lower() != spec["windowsComputerName"].lower():
        raise RuntimeError(_('Windows hostitel neodpovídá deklaraci.'))
    if data.get("hypervError") or not data.get("hypervModule"):
        raise RuntimeError((data.get("hypervError") or _('Hyper-V není dostupné.')))
    machines = [vm for vm in data["vms"] if vm["name"] == spec["name"]]
    if not machines:
        return {"state": "absent", "addresses": []}
    if len(machines) != 1:
        raise RuntimeError(_('Název VM není jednoznačný.'))
    vm = machines[0]
    if vm.get("ownerId") != spec["ownerId"]:
        raise RuntimeError(_('Hyper-V VM nemá deklarovaného vlastníka.'))
    adapters = vm["network"]
    if (vm["generation"] != spec["generation"] or len(adapters) != 1
            or mac(adapters[0]["mac"]) != mac(spec["mac"])
            or adapters[0]["switchName"] != spec["switchName"]):
        raise RuntimeError(_('Hyper-V VM neodpovídá deklaraci adaptéru a generace.'))
    state = vm["state"].lower()
    values = adapters[0]["ips"] if state == "running" else []
    return {"state": "shut off" if state == "off" else state,
            "addresses": addresses(values, excluded=(spec["sshHost"],))}


def libvirt_status(target, names=None):
    """Ověření konkrétní VM nad právě načteným seznamem místního libvirtu."""
    spec = target["spec"]
    if spec["uri"] != "qemu:///system":
        raise RuntimeError(_('Tento adaptér podporuje pouze místní libvirt.'))
    command = ["virsh", "--connect", spec["uri"]]
    if names is None:
        names = read(command + ["list", "--all", "--name"]).splitlines()
    if spec["name"] not in names:
        return {"state": "absent", "addresses": []}
    domain = ET.fromstring(read(command + ["dumpxml", spec["name"]]))
    if domain.findtext("uuid") != spec["uuid"] or domain.findtext("name") != spec["name"]:
        raise RuntimeError(_('Identita libvirt VM neodpovídá deklaraci.'))
    if "ownerId" in spec:
        marker = domain.find("./metadata/{urn:nixberry:deployment}owner")
        if marker is None or marker.get("id") != spec["ownerId"]:
            raise RuntimeError(_('Libvirt VM nemá deklarovanou značku vlastníka.'))
    adapters = domain.findall("./devices/interface/mac")
    if len(adapters) != 1 or mac(adapters[0].get("address", "")) != mac(spec["mac"]):
        raise RuntimeError(_('Síťový adaptér VM neodpovídá deklaraci.'))
    state = read(command + ["domstate", spec["name"]])
    values = []
    if state == "running":
        for line in read(command + ["domifaddr", spec["name"], "--source", "lease"]).splitlines():
            columns = line.split()
            if len(columns) == 4 and mac(columns[1]) == mac(spec["mac"]):
                values.append(columns[3])
        if not values:
            try:
                for line in read(command + ["domifaddr", spec["name"], "--source", "agent"]).splitlines():
                    columns = line.split()
                    if len(columns) == 4 and mac(columns[1]) == mac(spec["mac"]):
                        values.append(columns[3])
            except RuntimeError:
                pass
    return {"state": state, "addresses": addresses(values)}


def status(target):
    direct = direct_connection(target)
    if direct:
        snapshot = {"state": "running", "addresses": [direct["host"]]}
        hostname = target.get("guestHostName", target["spec"].get("name", "nixberry-vm"))
        output = read(ssh_command(target, snapshot) + [
            "set -eu; export PATH=/run/current-system/sw/bin:/run/wrappers/bin; "
            "uname -m; hostname; findmnt -n -o SOURCE /"
        ]).splitlines()
        if len(output) != 3 or output[0] != "x86_64" or output[1] != hostname:
            raise RuntimeError(_('Přímý SSH cíl neodpovídá deklarované architektuře a hostname VM.'))
        return {**snapshot, "hostname": output[1], "root": output[2]}
    if target.get("offline"):
        return {"state": "unconfigured", "addresses": []}
    spec = target["spec"]
    if target["backend"] in ("proxmox", "virtualbox", "vmware"):
        from portable import snapshot
        result = snapshot(target)
        result["addresses"] = addresses(result["addresses"], excluded=(spec.get("sshHost", ""),))
        return result
    if target["backend"] == "rpi":
        candidates = addresses([spec["address"]])
        if len(candidates) != 1:
            raise RuntimeError(_('RPi nemá povolenou deklarovanou IPv4 adresu.'))
        # Identitu ověřujeme z hardwaru, nikoli pouze z hostname nebo IP.
        script = (
            "set -eu; uname -m; "
            "tr -d '\\000' < /proc/device-tree/serial-number; printf '\\n'; "
            "hostname; ip -j link; findmnt -n -o SOURCE /"
        )
        output = read(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                       "--", "root@" + candidates[0], script]).splitlines()
        if len(output) != 5 or output[0] != "aarch64" or output[1] != spec["hardwareSerial"]:
            raise RuntimeError(_('Hardware RPi neodpovídá deklarovanému stroji.'))
        interfaces = json.loads(output[3])
        if not any(mac(item.get("address", "")) == mac(spec["lanMac"]) for item in interfaces):
            raise RuntimeError(_('Síťový adaptér RPi neodpovídá deklaraci.'))
        return {"state": "running", "addresses": candidates,
                "hostname": output[2], "hardwareSerial": output[1], "root": output[4]}
    if target["backend"] == "libvirt":
        return libvirt_status(target)
    if target["backend"] == "hyperv":
        data = json.loads(read([target["commands"]["probe"], "--status-only", "--vm-name", spec["name"]]))
        return hyperv_status(target, data)
    raise RuntimeError(_('Pro tento typ hostitele není čtení stavu implementované.'))


def deployment_method(target):
    method = target.get("deploymentMethod", "image")
    if method not in ("iso", "image"):
        raise ValueError(_('Metoda nasazení musí být iso nebo image.'))
    return method


def ssh_command(target, snapshot):
    if snapshot["state"] != "running":
        raise RuntimeError(_('Cíl neběží; přihlášení není dostupné.'))
    direct = direct_connection(target)
    # Omezujeme čekání na spojení a ztracený server, nikoli délku zdravé relace.
    command = ["ssh", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if direct:
        command += ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                    "-p", str(direct["port"])]
        if direct.get("knownHostsFile"):
            quoted = '"' + direct["knownHostsFile"].replace('\\', '\\\\').replace('"', '\\"') + '"'
            command += ["-o", "UserKnownHostsFile=" + quoted]
        if direct.get("hostKeyAlias"):
            command += ["-o", "HostKeyAlias=" + direct["hostKeyAlias"]]
        return command + ["--", direct["user"] + "@" + direct["host"]]
    candidates = addresses(snapshot["addresses"])
    if len(candidates) != 1:
        raise RuntimeError(_('Cíl nemá právě jednu použitelnou IPv4. Podrobnosti: probe CÍL.'))
    if target["backend"] in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
        from portable import known_hosts
        command += ["-o", "UserKnownHostsFile=" + str(known_hosts(target["spec"])),
                    "-o", "StrictHostKeyChecking=" + ("accept-new" if target.get("_acceptNewGuestKey") else "yes")]
        if target["backend"] == "hyperv":
            identity = target["spec"].get("uuid", target["spec"]["ownerId"])
            command += ["-o", "HostKeyAlias=nixberry-" + identity]
    if target["backend"] == "hyperv" or (target["backend"] in ("proxmox", "virtualbox", "vmware")
                                             and target["spec"].get("transport") == "ssh"):
        spec = target["spec"]
        if addresses([spec["sshHost"]]) != [spec["sshHost"]] or not re.fullmatch(
                r"[A-Za-z0-9_][A-Za-z0-9_.-]*", spec["sshUser"]):
            raise RuntimeError(_('Nepovolený SSH hostitel nebo účet.'))
        command += ["-J", spec["sshUser"] + "@" + spec["sshHost"]]
        if candidates[0] == spec["sshHost"]:
            raise RuntimeError(_('Adresa guest systému nesmí být adresa Windows hostitele.'))
    return command + ["--", "root@" + candidates[0]]


def installation_checks(target):
    """Společné rozlišení instalace na disku od instalačního nebo obnovovacího prostředí."""
    backend, spec = target["backend"], target["spec"]
    checks = [
        'test "$(findmnt -n -o FSTYPE /)" = ext4',
        'test "$(findmnt -n -o FSTYPE /boot)" = vfat',
        '! mountpoint -q /iso',
    ]
    if backend == "rpi":
        checks.append('test "$(lsblk -dn -o SERIAL /dev/$(lsblk -n -o PKNAME '
                      '$(findmnt -n -o SOURCE /)))" = ' + shlex.quote(spec["diskSerial"]))
    else:
        checks.append('test -d /sys/firmware/efi')
        manual = target.get("offline") and direct_connection(target)
        if manual:
            # Importér smí změnit řadič. Oddíly obrazu a jejich společný disk musí zůstat zachované.
            checks += [
                'test "$(readlink -f "$(findmnt -n -o SOURCE /)")" = '
                '"$(readlink -e /dev/disk/by-partlabel/disk-system-root)"',
                'test "$(readlink -f "$(findmnt -n -o SOURCE /boot)")" = '
                '"$(readlink -e /dev/disk/by-partlabel/disk-system-boot)"',
                'test -n "$(lsblk -ndo PKNAME "$(findmnt -n -o SOURCE /)")"',
                'test "$(lsblk -ndo PKNAME "$(findmnt -n -o SOURCE /)")" = '
                '"$(lsblk -ndo PKNAME "$(findmnt -n -o SOURCE /boot)")"',
            ]
        else:
            disk = "/dev/vda2" if backend in ("libvirt", "proxmox") else "/dev/sda2"
            checks.append('test "$(readlink -f $(findmnt -n -o SOURCE /))" = ' + shlex.quote(disk))
        if backend in ("proxmox", "virtualbox", "vmware") and not (
                target.get("offline") and direct_connection(target)):
            checks += [
                'test "$(tr A-Z a-z < /sys/class/dmi/id/product_uuid)" = ' + shlex.quote(spec["uuid"]),
                'grep -i -x -q -- ' + shlex.quote(spec["mac"]) + ' /sys/class/net/*/address',
            ]
    return checks


def verify(target, expected_system):
    snapshot = status(target)
    command = ssh_command(target, snapshot)
    backend, spec = target["backend"], target["spec"]
    hostname = spec["hostName"] if backend == "rpi" else target.get("guestHostName", "nixberry-vm")
    checks = [
        'test "$(readlink -f /run/current-system)" = ' + shlex.quote(expected_system),
        'test "$(hostname)" = ' + shlex.quote(hostname),
        *installation_checks(target),
        'test -z "$(systemctl --failed --no-legend --plain)"',
    ]
    services = ["sshd", "systemd-networkd"]
    if backend != "rpi" and not (target.get("offline") and direct_connection(target)):
        services += {"libvirt": ["qemu-guest-agent"], "proxmox": ["qemu-guest-agent"],
                     "hyperv": ["hv-kvp", "hv-vss"], "virtualbox": ["virtualbox"], "vmware": ["vmware"]}[backend]
    checks.extend("systemctl is-active --quiet " + service for service in services)
    checks = [check + " || { printf '%s\\n' " + shlex.quote(_("Kontrola neprošla: {check}").format(check=check))
              + "; exit 1; }" for check in checks]
    # Rozdíl generací je běžná situace před aktualizací, nikoli nečitelný shellový test.
    checks[0] = (
        'test "$(readlink -f /run/current-system)" = ' + shlex.quote(expected_system)
        + " || { printf '%s\\n' " + shlex.quote(_(
            "Běžící systém neodpovídá receptu.\nOčekávaný systém: {system}").format(system=expected_system))
        + "; printf " + shlex.quote(_("Běžící systém: %s") + "\n")
        + ' "$(readlink -f /run/current-system)"; exit 1; }'
    )
    checks += ["hostname", "readlink -f /run/current-system", "findmnt -n -o SOURCE /"]
    output = read([command[0], "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   *command[1:], "set -eu; " + "; ".join(checks)]).splitlines()
    if len(output) != 3:
        raise RuntimeError(_('Kontrola systému nevrátila očekávaný výsledek.'))
    return {"hostname": output[0], "system": output[1], "root": output[2],
            "addresses": snapshot["addresses"], "verified": True}
