#!/usr/bin/env python3
"""Společné ovládání deklarovaných RPi a VM: správa, nasazení a offline export."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from runtime import status, ssh_command, verify, read, guest_connection, direct_connection, capabilities, guest_host_option, deployment_method
from scaffold import new_libvirt, new_vm
from exporter import export
from updater import update
from diffing import compare
from inspection import doctor, exit_code
from cleanup import preview, failed
from discovery import collect_status
from messages import _, info, success, warning, ArgumentParser, error as report_error


def table(headers, rows):
    widths = [max(len(str(row[index])) for row in [headers, *rows])
              for index in range(len(headers))]
    for row in [headers, *rows]:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


def status_printer(registry, names):
    """Pevné šířky známých sloupců dovolí vypisovat řádky hned po odpovědi."""
    labels = {"running": _("běží"), "shut off": _("vypnutá"), "absent": _("nevytvořená"),
              "error": _("chyba"), "unconfigured": _("správa nenastavená")}
    headers = [_("Cíl"), _("Hypervizor"), _("Stav"), _("Adresa serveru")]
    hosts = {name: registry[name]["host"] if registry[name]["capabilities"]["hypervisor"] else _("nespravovaný")
             for name in names}
    columns = [names, list(hosts.values()), list(labels.values())]
    widths = [max([len(headers[index]), *(len(value) for value in column)]) for index, column in enumerate(columns)]
    def row(values):
        print("  ".join([*(str(value).ljust(width) for value, width in zip(values, widths)), str(values[-1])]).rstrip(), flush=True)
    row(headers)
    def result(name, data):
        row([name, hosts[name], labels.get(data["state"], data["state"]), ", ".join(data["addresses"]) or "—"])
        if "error" in data:
            report_error("{name}: {error}", name=name, error=data["error"])
    return result


def size(value):
    if value is None:
        return _("neznámá")
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def cleanup_table(report):
    info("{note}", note=report["note"])
    for name, result in report["targets"].items():
        print(_('\nGenerace: {name}').format(name=name))
        if "error" in result:
            report_error("{error}", error=result["error"])
            continue
        table([_("Generace"), _("Vytvořeno (UTC)"), _("Velikost closure"), _("Posouzení"), _("Ochrana")], [
            [row["generation"], datetime.fromtimestamp(row["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
             size(row["closureBytes"]), row["classification"], ", ".join(row["protectedBy"]) or "—"]
            for row in result["generations"]])
        info("{note}", note=result["note"])
        if result.get("sizeError"):
            warning(_("Nelze zjistit velikosti: {error}"), error=result["sizeError"])
    for name, result in report["hosts"].items():
        print(_('\nHostitel: {name}').format(name=name))
        if "error" in result:
            report_error("{error}", error=result["error"])
            continue
        table([_("VM"), _("Stav"), _("Alokace"), _("Posouzení")], [
            [vm.get("name", vm.get("path", "—")), vm.get("state", "unknown"),
             size(vm.get("allocatedBytes")), vm.get("classification", "unknown")]
            for vm in result.get("machines", [])])
        image_table(result.get("images", []))
        if result.get("note"):
            info("{note}", note=result["note"])
        for error in result.get("errors", []):
            report_error("{error}", error=error)
    if report["artifacts"]["images"]:
        print(_('\nObrazy v zadaných adresářích:'))
        image_table(report["artifacts"]["images"])
    for error in report["artifacts"]["errors"]:
        report_error("{error}", error=error)


def image_table(images):
    table([_("Obraz"), _("Alokace"), _("Posouzení"), _("Ochrana")], [
        [item["path"], size(item.get("allocatedBytes")), item.get("classification", "unknown"),
         ", ".join(item.get("protectedBy", [])) or "—"] for item in images])


def operations(target):
    caps = capabilities(target)
    result = ["show", "export"]
    if caps["system"]:
        result += ["status", "doctor", "ssh", "verify", "update", "diff", "diffg"]
    if caps["hypervisor"] or target["backend"] == "rpi":
        result += ["cleanup", *target.get("commands", {})]
        if "provision" in target.get("commands", {}) or target["backend"] == "rpi":
            result += ["reproduce", "plan"]
        if target["backend"] == "rpi":
            result += ["probe"]
    return result


HELP_COMMANDS = """Příkazy pro běžnou správu:
  list                         Místní evidence cílů a dostupných možností správy.
  show CÍL                     Deklarace, kapacita, síť a dostupné operace cíle.
  status [CÍL]                 Živý stav; hostitelé se dotazují souběžně, řádky přibývají.
  ssh CÍL [-- PŘÍKAZ ...]       Přihlášení nebo spuštění příkazu na serveru.
  verify CÍL                   Shoda běžícího systému s receptem, disk a služby.
  doctor [CÍL]                 Diagnostika jednoho nebo všech cílů.
  probe CÍL                    Podrobný průzkum platformy pro řešení problémů.

Aktualizace a porovnání:
  update CÍL                   Plán aktualizace; s --apply sestaví a aktivuje systém.
  diff CÍL                     Běžící systém proti aktuálnímu receptu PŘED aktualizací.
  diffg CÍL                    Dvě nejvyšší dochované generace na serveru.
                               diff sestavuje místně; diffg běží na serveru.

Nové deklarace VM (zapisují místní soubor, nevytvářejí VM):
  new-libvirt-vm JMÉNO          Deklarace VM pro libvirt.
  new-hyperv-vm JMÉNO           Deklarace VM pro Hyper-V.
  new-proxmox-vm JMÉNO          Deklarace Proxmox VM včetně upravitelné sítě.
  new-virtualbox-vm JMÉNO       Deklarace VM pro VirtualBox.
  new-vmware-vm JMÉNO           Deklarace VM pro VMware Workstation.
                               --host vybírá hostitele; jediný dostupný se vybere automaticky.
                               --offline připraví kterýkoli backend bez hypervizoru.
                               --configuration JMÉNO vybírá konfiguraci serveru; výchozí je default.
                               --dry-run soubor nezapíše. Názvy new-* bez -vm jsou aliasy.

Nasazení, obnova a offline přenos (podle možností platformy):
  plan CÍL                     Plán vytvoření VM nebo obnovy RPi bez zápisu.
  reproduce CÍL                Úplné nasazení/obnova; provedení vyžaduje --apply.
  create CÍL                   Vytvoření VM; provedení vyžaduje --apply.
  install CÍL                  Dokončení nasazení podle metody; vyžaduje --apply.
  provision CÍL                Vytvoření a instalace; provedení vyžaduje --apply.
  reset CÍL                    Odstranění povolené VM na všech pěti backendech.
                               Vyžaduje allowReset = true v instanci; bez --apply jen ověřuje plán.
  export CÍL --output ADRESÁŘ   Offline balíček s diskem nebo ISO a receptem do nového adresáře.
  cleanup [CÍL]                Náhled kandidátů na úklid, nic nemaže.
                               --artifact-root ADRESÁŘ přidá kontrolu obrazů.

Použití a režimy:
  Bez příkazu se vypíše list. CÍL je jméno deklarace, nikoli IP adresa.
  Dostupné operace konkrétního cíle ukáže show CÍL.
  update/create/install/provision/reproduce/reset bez --apply pouze plánují.
  new-* a export běžně zapisují místně; --dry-run zobrazí plán bez zápisu.
  diff/diffg mohou číst server a diff sestavuje recept; --dry-run vypíše jen plán.
  --ssh root@HOST:PORT přepíše SSH přístup k offline VM libovolného backendu.
  --method image je výchozí pro všechny VM: import hotového disku a ověření startu.
  --method iso instaluje na prázdný disk, potom ISO odpojí a ověří start systému.
  Volbu lze uložit jako deploymentMethod v deklaraci; --method ji přepíše.
  Platí pro new-*, show, plan, create, install, provision, reproduce a export.
  --offline vybírá přípravu bez hypervizoru nezávisle na metodě. RPi používá SD.
  diff ukáže připravené změny. Po aktualizaci je obvykle prázdný; použij diffg.

Příklady:
  nixberry list
  nixberry show vm-demo-libvirt
  nixberry diff vm-demo-libvirt
  nixberry update vm-demo-libvirt --apply
  nixberry diffg vm-demo-libvirt
  nixberry ssh vm-demo-libvirt -- uname -r
  nixberry new-proxmox-vm vm-demo-proxmox --host HOSTITEL
  nixberry plan vm-demo-proxmox
  nixberry reproduce vm-demo-proxmox --apply
  nixberry new-proxmox-vm vm-demo-proxmox-offline --offline
  nixberry export vm-demo-proxmox-offline --output ~/vm-demo-proxmox-offline
  nixberry update vm-demo-proxmox-offline --ssh root@10.0.1.19:22 --apply
  nixberry plan nixberry --address 192.168.50.20

Podrobný provozní návod: README.md v kořeni receptu nixberry.
Úplná reference příkazů a voleb: docs/cli.md.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    remote_command = []
    if "--" in argv:
        delimiter = argv.index("--")
        argv, remote_command = argv[:delimiter], argv[delimiter + 1:]
    parser = ArgumentParser(
        prog="nixberry", description=_('Společné ovládání deklarovaných RPi a VM: správa, nasazení a offline export.'), epilog=_(HELP_COMMANDS),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="nixberry " + os.environ.get("NIXBERRY_VERSION", "development"))
    parser.add_argument("action", metavar=_("PŘÍKAZ"), help=_('Příkaz z přehledu níže (výchozí: list)'), nargs="?", default="list",
                        type=lambda value: value + "-vm" if value in ("new-libvirt", "new-hyperv", "new-proxmox", "new-virtualbox", "new-vmware") else value, choices=(
        "list", "show", "status", "ssh", "probe", "plan", "create", "install", "provision", "reset",
        "new-libvirt-vm", "new-hyperv-vm", "new-proxmox-vm", "new-virtualbox-vm", "new-vmware-vm", "reproduce", "verify", "update", "diff", "diffg", "export", "doctor", "cleanup"))
    parser.add_argument("target", metavar=_("CÍL"), nargs="?", help=_('Jméno existujícího cíle, u new-* nové jméno'))
    parser.add_argument("--host", help=_('Hostitel hypervizoru pro new-*; pro správu VM přímé SSH root@HOST[:PORT]'))
    parser.add_argument("--offline", action="store_true", help=_('Offline příprava libovolné VM bez hypervizoru; nelze kombinovat s --host'))
    parser.add_argument("--configuration", help=_('Konfigurace serveru pro new-*; výchozí default, další názvy z configurations/'))
    parser.add_argument("--method", choices=("iso", "image"), help=_('Metoda nasazení VM: instalace z ISO, nebo import hotového disku; přepíše deploymentMethod z deklarace'))
    parser.add_argument("--ssh", help=_('Jednorázový SSH endpoint serveru root@HOST[:PORT]; přepíše access.host a access.port'))
    parser.add_argument("--output", help=_('Nový adresář offline balíčku; relativní cesta se počítá od pracovního adresáře'))
    parser.add_argument("--address", help=_('Aktuální IP instalační SD, pouze pro reprodukci RPi'))
    parser.add_argument("--erase-existing", action="store_true", help=_('Zahrnout vymazání SSD při obnově RPi'))
    parser.add_argument("--json", action="store_true", help=_('Strojový výstup pro inventuru, diagnostiku, verify a export'))
    parser.add_argument("--artifact-root", action="append", default=[],
                        help=_('Adresář obrazů pro náhled cleanup; lze opakovat, odkazy se nepřecházejí'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help=_('Provést změny u update a operací nasazení/obnovy'))
    modes.add_argument("--dry-run", action="store_true", help=_('Zobrazit plán bez zápisu; diff/diffg bez připojení a sestavení'))
    args = parser.parse_args(argv)
    # Staré názvy zůstávají aliasy; adaptéry dál dostávají původní název backendu.
    if args.action.startswith("new-") and args.action.endswith("-vm"):
        args.action = args.action[:-3]
    if args.offline and (not args.action.startswith("new-") or args.host):
        parser.error(_('--offline patří k new-*-vm bez --host.'))
    if args.configuration is not None and not args.action.startswith("new-"):
        parser.error(_('--configuration patří pouze k new-*-vm; u existujícího cíle uprav configuration v deklaraci.'))
    if args.method and not (args.action.startswith("new-") or args.action in ("show", "plan", "create", "install", "provision", "reproduce", "export")):
        parser.error(_('--method patří k new-*, show, plan, create, install, provision, reproduce a export.'))
    if remote_command and args.action != "ssh":
        parser.error(_('Příkaz za -- je dostupný pouze pro ssh CÍL -- PŘÍKAZ.'))
    registry = json.loads(Path(os.environ["NIXBERRY_DEPLOYMENT"]).read_text())
    if args.ssh and (args.host or args.action.startswith("new-")):
        parser.error(_('--ssh patří ke správě serveru a nelze jej kombinovat s --host nebo new-*.'))
    override = args.ssh or (args.host if not args.action.startswith("new-") else None)
    if override:
        if args.action not in ("update", "ssh", "verify", "status", "doctor", "diff", "diffg"):
            parser.error(_('Přímé SSH podporuje update, ssh, verify, status, doctor, diff a diffg.'))
        if (args.target not in registry or registry[args.target]["backend"] not in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware")
                or not registry[args.target].get("offline")):
            parser.error(_('Přímé SSH vyžaduje offline deklaraci VM bez správy hypervizoru.'))
        try:
            direct = guest_connection(override)
        except ValueError as error:
            parser.error(str(error))
        registry[args.target] = {**registry[args.target], "_guestConnection": direct}
    for target in registry.values():
        try:
            target["capabilities"] = capabilities(target)
            target["operations"] = operations(target)
        except ValueError as error:
            parser.error(str(error))
    if (args.target in registry and args.action not in ("list", "show", "export", "status", "doctor")
            and not args.action.startswith("new-") and args.action not in registry[args.target]["operations"]):
        parser.error(_('Operace není dostupná. Pro správu systému nastav access nebo použij --ssh root@HOST:PORT; ovládání hypervizoru vyžaduje deklarovaný host.'))
    if args.artifact_root and args.action != "cleanup":
        parser.error(_('--artifact-root patří pouze k cleanup.'))
    if args.output and args.action != "export":
        parser.error(_('--output patří pouze k export.'))
    if args.action.startswith("new-"):
        if args.apply or args.json or args.address or args.erase_existing:
            parser.error(_('new-* vytvoří pouze místní deklaraci; tyto volby nepřijímá.'))
        if not args.target:
            parser.error(_('Zadej jméno nového cíle: {value1} JMÉNO.').format(value1=args.action + "-vm"))
        hosts = json.loads(Path(os.environ["NIXBERRY_DEPLOYMENT_HOSTS"]).read_text())
        if args.action == "new-libvirt" and not args.offline:
            path, text = new_libvirt(os.environ.get("NIXBERRY_RECIPE", ""), hosts, registry,
                                    args.target, args.host, write=not args.dry_run, **({"method": args.method} if args.method else {}),
                                    **({"configuration": args.configuration} if args.configuration is not None else {}))
        else:
            path, text = new_vm(os.environ.get("NIXBERRY_RECIPE", ""), hosts, registry,
                                args.target, args.action[4:], args.host, write=not args.dry_run, offline=args.offline,
                                **({"method": args.method} if args.method else {}),
                                **({"configuration": args.configuration} if args.configuration is not None else {}))
        if args.dry_run:
            info(_("Plán deklarace: {path}"), path=path)
        else:
            success(_("Vytvořena deklarace: {path}"), path=path)
            print(path)
        info(_('Konfigurace serveru: {configuration}'), configuration=args.configuration or "default")
        if args.dry_run:
            print(text)
        info(_('Před sestavením zkontroluj network: DHCP, nebo mode = "static" s address, gateway a dns.'))
        if args.offline:
            info(_("Offline deklarace: před exportem uprav kapacitu a parametry nasazení v souboru."))
            info(_("Další krok: {command}"), command=f"nixberry export {args.target} --output ~/{args.target}-offline")
        else:
            info(_("Další krok: {command}"), command=f"nixberry plan {args.target}")
            info(_("Po kontrole: {command}"), command=f"nixberry reproduce {args.target} --apply")
        return 0
    if (args.address or args.erase_existing) and not (
            args.action in ("plan", "reproduce") and args.target in registry
            and registry[args.target]["backend"] == "rpi"):
        parser.error(_('--address a --erase-existing patří pouze k plan/reproduce cíle RPi.'))
    if args.json and args.action not in ("list", "show", "status", "verify", "export", "doctor", "cleanup"):
        parser.error(_('--json je dostupné pro list, show, status, verify, export, doctor a cleanup.'))
    if args.action in ("list", "show", "status", "ssh", "probe", "plan", "verify", "export", "diff", "diffg", "doctor", "cleanup") and args.apply:
        parser.error(_('Tato operace nepřijímá --apply.'))
    if args.action in ("doctor", "cleanup"):
        if args.target and args.target not in registry:
            parser.error(_('Neznámý cíl. Dostupné cíle: {value1}').format(value1=", ".join(sorted(registry))))
        names = [args.target] if args.target else sorted(registry)
        if args.action == "doctor":
            report = {}
            for name in names:
                try:
                    report[name] = doctor(registry[name])
                except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
                    report[name] = {"checks": [{"name": "inspection", "level": "error", "message": str(error)}]}
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                table([_("Cíl"), _("Kontrola"), _("Výsledek"), _("Podrobnosti")], [
                    [name, check["name"], check["level"], check["message"] +
                     (" " + check["hint"] if "hint" in check else "")]
                    for name, result in report.items() for check in result["checks"]])
            return exit_code(report)
        report = preview(registry, names, args.artifact_root)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            cleanup_table(report)
        return int(failed(report))
    if args.action == "list":
        if args.target or args.apply or args.dry_run:
            parser.error(_('list nepřijímá cíl ani režim zápisu.'))
        if args.json:
            print(json.dumps({name: {key: value for key, value in target.items() if key != "commands"}
                              for name, target in registry.items()}, ensure_ascii=False, indent=2))
        else:
            if not registry:
                info(_("Nejsou deklarované žádné cíle. Přidej hostitele a instanci nebo cíl RPi."))
                return 0
            table([_("Cíl"), _("Platforma"), _("Konfigurace"), _("Sestavení/export"), _("Správa systému"), _("Hypervizor")], [
                [name, target["backend"], target.get("configuration", "default"), _("ano"), _("nastavená") if target["capabilities"]["system"] else _("nenastavená"),
                 target["host"] if target["capabilities"]["hypervisor"] else _("nespravovaný")]
                for name, target in sorted(registry.items())])
            info(_("Stav všech cílů: {command}"), command="nixberry status")
            info(_("Podrobnosti cíle: {command}"), command="nixberry show CÍL")
        return 0
    if args.action == "status":
        if args.target and args.target not in registry:
            parser.error(_('Neznámý cíl. Dostupné cíle: {value1}').format(value1=", ".join(sorted(registry))))
        names = [args.target] if args.target else sorted(registry)
        snapshots = collect_status(registry, names, status,
                                   on_result=None if args.json else status_printer(registry, names))
        if args.json:
            print(json.dumps(snapshots, ensure_ascii=False, indent=2))
        return int(any("error" in data for data in snapshots.values()))
    if args.target not in registry:
        parser.error(_('Vyber cíl: {value1}. Libovolná adresa není povolená.').format(value1=", ".join(sorted(registry))))
    target = registry[args.target]
    if args.method and target["backend"] == "rpi":
        parser.error(_('--method je určené pro VM; RPi používá obnovovací SD.'))
    if args.action == "show" and args.method:
        target = {**target, "deploymentMethod": args.method}
    if args.action in ("diff", "diffg"):
        return compare(os.environ.get("NIXBERRY_RECIPE", ""), args.target, target,
                       generations=args.action == "diffg", dry_run=args.dry_run)
    if args.action == "reset" and target.get("allowReset", target["spec"].get("allowReset", False)) is not True:
        parser.error(_('Odstranění není povolené. Nastav allowReset = true; v deklaraci této VM v instances/, potom zkontroluj reset CÍL bez --apply.'))
    if args.action == "update":
        info(_('Konfigurace serveru: {configuration}'), configuration=target.get("configuration", "default"))
        update(os.environ.get("NIXBERRY_RECIPE", ""), args.target, target, apply=args.apply)
        return 0
    if args.action == "export":
        info(_('Konfigurace serveru: {configuration}'), configuration=target.get("configuration", "default"))
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe or not args.output:
            parser.error(_('Použití: nixberry export CÍL --output NOVÝ_ADRESÁŘ.'))
        result = export(recipe, args.target, target, args.output, dry_run=args.dry_run,
                        **({"method": args.method} if args.method else {}))
        if args.json or args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            success(_("Offline balíček připravený: {path}"), path=result["destination"])
            print(result["destination"])
            info(_("Před přenosem ověř SHA256SUMS; postup pro zvolené médium je v README.txt."))
        return 0
    if args.action == "verify":
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe:
            parser.error(_('verify spusť přes spouštěč nixberry z kořene projektu.'))
        expected = read(["nix", "eval", "--raw", "--no-write-lock-file",
                         "path:" + recipe + "#nixberrySystems." + args.target + ".config.system.build.toplevel.outPath"])
        try:
            result = verify(target, expected)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            raise RuntimeError(_("Ověření cíle {name} neprošlo: {error}\n"
                                 "Diagnostika: {doctor}\nPorovnání s receptem: {diff}").format(
                                     name=args.target, error=error,
                                     doctor=f"nixberry doctor {args.target}{guest_host_option(target)}",
                                     diff=f"nixberry diff {args.target}{guest_host_option(target)}")) from error
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            success(_("Ověřeno: {name}, {hostname}, systém z {root}."),
                    name=args.target, hostname=result["hostname"], root=result["root"])
            print(result["system"])
        return 0
    if args.action in ("plan", "create", "install", "provision", "reproduce"):
        info(_('Konfigurace serveru: {configuration}'), configuration=target.get("configuration", "default"))
    if args.action in ("plan", "reproduce") and target["backend"] == "rpi":
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe:
            parser.error(_('Reprodukci RPi spusť přes spouštěč nixberry z kořene projektu.'))
        command = ["nix", "run", "--no-write-lock-file", "path:" + recipe + "#nixberry-rpi-install",
                   "--", "--target", args.target,
                   "--apply" if args.apply else "--dry-run"]
        if args.address:
            command += ["--address", args.address]
        if args.erase_existing:
            command.append("--erase-existing")
        return subprocess.run(command, check=False).returncode
    if args.action == "show":
        if args.json:
            print(json.dumps({key: value for key, value in target.items() if key != "commands"},
                             ensure_ascii=False, indent=2))
        else:
            spec = target["spec"]
            caps = target["capabilities"]
            print(_('Cíl: {value1}\nPlatforma: {value2}').format(value1=args.target, value2=target["backend"]))
            print(_('Konfigurace: {configuration}').format(configuration=target.get("configuration", "default")))
            print(_('Hypervizor: {value1}').format(value1=target["host"] if caps["hypervisor"] else _("nespravovaný")))
            if target["backend"] != "rpi":
                print(_('Metoda nasazení: {method}').format(method=args.method or deployment_method(target)))
            print(_('Sestavení/export: ano; správa systému: {value1}').format(value1=_("nastavená") if caps["system"] else _("nenastavená")))
            print(_('Profil platformy: {value1}').format(value1=target["profile"]))
            if target["backend"] != "rpi":
                print(_('Povolení odstranění: {value}').format(value=_("ano") if target.get("allowReset", False) else _("ne")))
            if target["backend"] == "rpi":
                print(_('RPi: {value1}\nAdresa: {value2}').format(value1=spec["hostName"], value2=spec["address"]))
                print(_('Sériové číslo: {value1}\nDisk: {value2}').format(value1=spec["hardwareSerial"], value2=spec["diskDevice"]))
            else:
                print(_('VM: {value1}').format(value1=spec["name"]))
                print(_('Kapacita: {value1} CPU, {value2} MiB RAM, {value3} GiB disk').format(value1=spec["vcpus"], value2=spec["memoryMiB"], value3=spec["diskGiB"]))
                if target["backend"] == "proxmox":
                    print(_('Import: VMID {value1}, úložiště {value2}, bridge {value3}').format(value1=spec["vmid"], value2=spec["storage"], value3=spec["bridge"]))
                if target.get("offline"):
                    info(_("VM vytváří provozovatel; obraz lze připravit přes offline export."))
                direct = direct_connection(target)
                if direct:
                    print(_('SSH serveru: {value1}@{value2}:{value3}').format(value1=direct["user"], value2=direct["host"], value3=direct["port"]))
                    print(_('Ověření klíče: striktní known_hosts{value1}{value2}').format(value1=", soubor " + direct["knownHostsFile"] if direct.get("knownHostsFile") else "", value2=", alias " + direct["hostKeyAlias"] if direct.get("hostKeyAlias") else ""))
                network = target.get("network", {"mode": "dhcp"})
                if network.get("mode", "dhcp") == "static":
                    print(_('Síť: {value1}, brána {value2}, DNS {value3}').format(value1=network["address"], value2=network["gateway"], value3=", ".join(network["dns"])))
                else:
                    print(_('Síť: DHCP'))
            print(_('Operace: {value1}').format(value1=", ".join(operations(target))))
            if not caps["system"]:
                info(_("Export: {command}"), command=f"nixberry export {args.target} --output ~/{args.target}-offline")
            else:
                info(_("Živý stav: {command}"), command="nixberry status " + args.target)
        return 0
    if args.action == "ssh":
        if args.dry_run:
            parser.error(_('ssh nepřijímá --dry-run; stav zjistíš přes status.'))
        command = ssh_command(target, status(target))
        if not remote_command and sys.stdin.isatty():
            command[1:1] = ["-e", "~"]
            info(_("Ctrl+C přerušuje vzdálený příkaz. Zaseknutou relaci odpojíš: Enter, potom ~."))
        info(_("Připojuji: {command}"), command=" ".join(command))
        try:
            return subprocess.run(command + remote_command, check=False).returncode
        except KeyboardInterrupt:
            return 130
    if args.action == "probe" and target["backend"] == "rpi":
        print(json.dumps(status(target), ensure_ascii=False, indent=2))
        return 0
    action = "provision" if args.action in ("plan", "reproduce") else args.action
    if action not in target["commands"]:
        parser.error(_('Operace {action} pro {value2} není implementovaná. Dostupné: {value3}').format(action=action, value2=args.target, value3=", ".join(target["commands"])))
    command = [target["commands"][action]]
    if args.method:
        command += ["--method", args.method]
    if action not in ("probe", "reset") and target["backend"] != "rpi":
        info(_('Metoda nasazení: {method}'), method=args.method or deployment_method(target))
    if action != "probe":
        command.append("--apply" if args.apply else "--dry-run")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
