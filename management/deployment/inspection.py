"""Diagnostika a přehled generací pouze čtením, bez sestavení nebo aktivace."""

from messages import _

import base64
import json
import re
import shlex
import subprocess

from runtime import installation_checks, read, status, direct_connection, capabilities
from updater import connection


def guest_services(backend):
    return ["sshd", "systemd-networkd"] + {
        "rpi": [], "libvirt": ["qemu-guest-agent"], "proxmox": ["qemu-guest-agent"],
        "hyperv": ["hv-kvp", "hv-vss"], "virtualbox": ["virtualbox"],
        "vmware": ["vmware"],
    }[backend]


def shell_probe(command, probes):
    """Každá kontrola má vlastní návratový kód; chyba jedné nezakryje ostatní."""
    lines = []
    for key, script in probes.items():
        if not re.fullmatch(r"[a-z_]+", key):
            raise ValueError(_('Neplatný klíč kontroly.'))
        lines.append("out=$( ( " + script + " ) 2>&1); rc=$?; "
                     + "printf '%s %s ' " + shlex.quote(key) + ' "$rc"; '
                     + 'printf %s "$out" | base64 -w0; printf "\\n"')
    output = read(command + ["\n".join(lines)])
    result = {}
    for line in output.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 2:
            raise RuntimeError(_('Neúplná odpověď diagnostiky.'))
        key, rc = parts[:2]
        if key not in probes or key in result:
            raise RuntimeError(_('Neočekávaná odpověď diagnostiky.'))
        result[key] = {"code": int(rc), "output": base64.b64decode(
            parts[2] if len(parts) == 3 else "", validate=True).decode("utf-8", errors="replace")}
    if result.keys() != probes.keys():
        raise RuntimeError(_('Chybí část odpovědi diagnostiky.'))
    return result


def doctor(target):
    result = {"checks": [], "snapshot": None}

    def check(name, level, message, **details):
        result["checks"].append({"name": name, "level": level, "message": message, **details})

    if target.get("offline") and not capabilities(target)["system"]:
        check("connection", "warning", _('Správa systému není nastavená; doplň access nebo použij --ssh.'))
        return result
    try:
        snapshot = status(target)
        result["snapshot"] = snapshot
        check("identity", "ok", _('Identita cíle odpovídá deklaraci.'))
        if snapshot["state"] != "running":
            check("state", "warning", _('Cíl neběží: {value1}').format(value1=snapshot["state"]))
            return result
        command = connection(target, snapshot)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        check("connection", "error", str(error), hint=(
            _('Ověř SSH klíč a známý klíč hostitele; při síťové chybě také kabel, port a MAC v deklaraci. Diagnostika neskenuje síť ani neobchází kontrolu identity.')))
        return result
    services = guest_services(target["backend"])
    probes = {
        "hostname": "hostname", "architecture": "uname -m",
        "current": "readlink -e /run/current-system",
        "booted": "readlink -e /run/booted-system",
        "default": "readlink -e /nix/var/nix/profiles/system",
        "failed": "systemctl --failed --no-legend --plain --no-pager",
        "services": "systemctl is-active " + " ".join(services),
        "disks": "df -P / /boot /nix", "inodes": "df -Pi / /boot /nix",
        "mounts": "findmnt -J /", "routes": "ip -j route", "interfaces": "ip -j address",
        "memory": "cat /proc/meminfo",
        "uptime": "cat /proc/uptime",
        "installation": " && ".join(installation_checks(target)),
        "temperature": "for p in /sys/class/thermal/thermal_zone*/temp; do "
                       'if test -r "$p"; then printf "%s " "$p"; cat "$p"; fi; done',
        "reboot": 'test -d /run/booted-system || exit 2; '
                  'for p in kernel initrd; do test "$(readlink -f /run/booted-system/$p)" = '
                  '"$(readlink -f /run/current-system/$p)" || exit 1; done; '
                  'cmp -s /run/booted-system/kernel-params /run/current-system/kernel-params',
    }
    try:
        data = shell_probe(command, probes)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        check("guest", "error", str(error))
        return result
    result["observations"] = data
    for name, item in data.items():
        if item["code"] and name not in ("services", "reboot", "installation"):
            check(name, "warning", _('Kontrola se nezdařila: {value1}').format(value1=item["output"]))
    for name, expected in (("hostname", target["spec"].get("hostName") if target["backend"] == "rpi"
                            else target.get("guestHostName", "nixberry-vm")),
                           ("architecture", "aarch64" if target["backend"] == "rpi" else "x86_64")):
        if not data[name]["code"]:
            actual = data[name]["output"]
            check(name, "ok" if actual == expected else "error", _('{actual} (očekáváno {value2})').format(actual=actual, value2=str(expected)))
    if not data["failed"]["code"]:
        failed = data["failed"]["output"]
        check("failed", "error" if failed else "ok", (failed or _('Žádné neúspěšné jednotky.')))
    active = data["services"]["output"].splitlines()
    for index, service in enumerate(services):
        state = active[index] if index < len(active) else "unknown"
        optional_agent = (service == "qemu-guest-agent" and target.get("offline")
                          and direct_connection(target))
        level = "ok" if state == "active" else "warning" if optional_agent else "error"
        check("service:" + service, level, state)
    check("installation", "ok" if not data["installation"]["code"] else "error",
          (_('Kořenový a bootovací oddíl odpovídají instalaci.') if not data["installation"]["code"] else _('Disk nebo prostředí neodpovídá instalaci; může jít o instalační ISO či obnovovací SD.')))
    if not data["memory"]["code"]:
        memory = {key: int(value) for key, value in re.findall(r"^(MemTotal|MemAvailable):\s+(\d+)",
                                                              data["memory"]["output"], re.M)}
        if memory.get("MemTotal") and "MemAvailable" in memory:
            available = 100 * memory["MemAvailable"] / memory["MemTotal"]
            check("memory", "warning" if available < 5 else "ok",
                  _('Dostupná paměť: {available:.1f} %').format(available=available), availableBytes=memory["MemAvailable"] * 1024)
    if not data["temperature"]["code"]:
        for line in data["temperature"]["output"].splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("-").isdigit():
                temperature = int(parts[1]) / 1000
                check("temperature:" + parts[0], "warning" if temperature >= 80 else "ok",
                      _('{temperature:.1f} °C (orientační práh 80 °C)').format(temperature=temperature))
    for name in ("disks", "inodes"):
        if data[name]["code"]:
            continue
        seen = set()
        for line in data[name]["output"].splitlines()[1:]:
            fields = line.split()
            if len(fields) == 6 and fields[5] in seen:
                continue
            if len(fields) == 6:
                seen.add(fields[5])
            if name == "inodes" and len(fields) == 6 and fields[4] == "-":
                check(name + ":" + fields[5], "ok", _('{value1}: souborový systém neudává inody.').format(value1=fields[5]))
                continue
            if len(fields) != 6 or not re.fullmatch(r"\d+%", fields[4]):
                check(name, "warning", _('Neznámý formát df: {line}').format(line=line))
                continue
            used = int(fields[4][:-1])
            check(name + ":" + fields[5], "error" if used >= 98 else "warning" if used >= 90 else "ok",
                  f"{fields[5]}: {used} %", usedPercent=used)
    if all(not data[key]["code"] for key in ("current", "default")):
        check("generation", "ok" if data["current"]["output"] == data["default"]["output"] else "warning",
              _("Aktivní a výchozí systém odpovídají.") if data["current"]["output"] ==
              data["default"]["output"] else _("Aktivní a výchozí systém se liší; po restartu může naběhnout jiný systém."))
    check("reboot", "ok" if data["reboot"]["code"] == 0 else "warning",
          (_('Bootovací konfigurace odpovídá.') if data["reboot"]["code"] == 0 else _('Bootovací konfigurace se liší nebo ji nelze porovnat; prověř potřebu restartu.')))
    if not data["routes"]["code"]:
        try:
            routes = json.loads(data["routes"]["output"])
            check("route", "ok" if any(r.get("dst") == "default" for r in routes) else "warning",
                  (_('Výchozí trasa je přítomná.') if any(r.get("dst") == "default" for r in routes) else _('Chybí výchozí trasa; může jít o záměrně izolovanou síť.')))
        except (ValueError, TypeError):
            check("route", "warning", _('Neplatná odpověď ip route.'))
    return result


def generations(target):
    snapshot = status(target)
    command = connection(target, snapshot)
    probes = {
        "current": "readlink -e /run/current-system",
        "booted": "readlink -e /run/booted-system",
        "default": "readlink -e /nix/var/nix/profiles/system",
        "generations": "for p in /nix/var/nix/profiles/system-*-link; do test -L \"$p\" || continue; "
                       'printf "%s\\t%s\\t%s\\n" "$p" "$(readlink -e "$p")" "$(stat -c %Y "$p")"; done',
    }
    data = shell_probe(command, probes)
    if any(item["code"] for item in data.values()):
        raise RuntimeError(_('Nelze bezpečně zjistit všechny chráněné generace.'))
    rows = []
    for line in data["generations"]["output"].splitlines():
        path, system, timestamp = line.split("\t")
        number = re.fullmatch(r"/nix/var/nix/profiles/system-(\d+)-link", path)
        if not number or not re.fullmatch(r"/nix/store/[a-z0-9]{32}-nixos-system-[A-Za-z0-9+._-]+", system):
            raise RuntimeError(_('Neplatná nebo neúplná evidence generací.'))
        rows.append({"generation": int(number[1]), "path": path, "system": system,
                     "timestamp": int(timestamp), "protectedBy": []})
    rows.sort(key=lambda row: row["generation"], reverse=True)
    current = data["current"]["output"]
    previous = next((row["system"] for row in rows if row["system"] != current), None)
    paths = sorted({row["system"] for row in rows})
    sizes = {}
    size_error = None
    if paths:
        try:
            raw = json.loads(read(command + ["nix path-info --offline --json --closure-size " + shlex.join(paths)]))
            sizes = ({item["path"]: item for item in raw} if isinstance(raw, list) else raw)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            size_error = str(error)
    for row in rows:
        row["protectedBy"] = [key for key in ("current", "booted", "default")
                              if row["system"] == data[key]["output"]]
        if row["system"] == previous:
            row["protectedBy"].append("rollback")
        row["classification"] = "protected" if row["protectedBy"] else "review"
        row["closureBytes"] = sizes.get(row["system"], {}).get("closureSize")
    return {"generations": rows, "sizeError": size_error,
            "note": _('Velikosti zahrnují sdílené závislosti; nelze je sčítat ani považovat za uvolnitelné místo. review znamená ruční posouzení, nikoli povolení smazat. Všechny dochované generace drží GC roots.')}


def exit_code(report):
    levels = {check["level"] for result in report.values() for check in result["checks"]}
    return 1 if "error" in levels else 2 if "warning" in levels else 0
