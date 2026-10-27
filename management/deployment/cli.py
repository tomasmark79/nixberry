#!/usr/bin/env python3
"Unified management of declared RPi and VMs: operations, deployment and offline export."

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
from recipe_source import materialized_recipe
from discovery import collect_status
from security import policy as security_policy, require_supported, prepare_image, image_metadata, unlock
from messages import _, info, success, warning, ArgumentParser, error as report_error
from backups import ACTIONS as BACKUP_ACTIONS, available as backup_operations, run as run_backup

BACKUP_HELP = """\nEncrypted backup commands:
  backup TARGET          Review coordinated capture; --apply creates an encrypted backup.
  backups TARGET         List locally recorded snapshots without contacting the source.
  verify-backup TARGET   Fully authenticate --snapshot ID without contacting the source.
  restore TARGET         Plan data recovery with --from SOURCE --snapshot ID; --apply publishes data.
  recover-source TARGET  Recover configuration with --snapshot ID --output DIR --apply.
  backup-prune TARGET    Preview keepLast per machine identity; --apply removes verified old copies.
  backup-schedule TARGET Export reviewable user units with --output DIR; never enables them.
  backup-check TARGET    Check backup freshness and run history; --apply records and notifies.
  backup-monitor TARGET  Export monitoring-only user units with --output DIR; never enables them.
\nBackup policy lives in the target declaration. Keys remain runtime files outside Git and the Nix store.
Restore does not create a VM, activate configuration or reboot. See docs/backups.md.
"""


def table(headers, rows):
    widths = [max(len(str(row[index])) for row in [headers, *rows])
              for index in range(len(headers))]
    for row in [headers, *rows]:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


def status_printer(registry, names):
    """Fixed widths for known columns allow rows to appear as responses arrive."""
    labels = {"running": _("running"), "shut off": _("stopped"), "absent": _("absent"),
              "error": _("error"), "unconfigured": _("management not configured")}
    headers = [_("Target"), _("Hypervisor"), _("State"), _("Server address")]
    hosts = {name: registry[name]["host"] if registry[name]["capabilities"]["hypervisor"] else _("unmanaged")
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
        return _("unknown")
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def cleanup_table(report):
    info("{note}", note=report["note"])
    for name, result in report["targets"].items():
        print(_("\nGeneration: {name}").format(name=name))
        if "error" in result:
            report_error("{error}", error=result["error"])
            continue
        table([_("Generation"), _("Created (UTC)"), _("Closure size"), _("Assessment"), _("Protection")], [
            [row["generation"], datetime.fromtimestamp(row["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
             size(row["closureBytes"]), row["classification"], ", ".join(row["protectedBy"]) or "—"]
            for row in result["generations"]])
        info("{note}", note=result["note"])
        if result.get("sizeError"):
            warning(_("Cannot determine sizes: {error}"), error=result["sizeError"])
    for name, result in report["hosts"].items():
        print(_("\nHost: {name}").format(name=name))
        if "error" in result:
            report_error("{error}", error=result["error"])
            continue
        table([_("VM"), _("State"), _("Allocation"), _("Assessment")], [
            [vm.get("name", vm.get("path", "—")), vm.get("state", "unknown"),
             size(vm.get("allocatedBytes")), vm.get("classification", "unknown")]
            for vm in result.get("machines", [])])
        image_table(result.get("images", []))
        if result.get("note"):
            info("{note}", note=result["note"])
        for error in result.get("errors", []):
            report_error("{error}", error=error)
    if report["artifacts"]["images"]:
        print(_("\nImages in the selected directories:"))
        image_table(report["artifacts"]["images"])
    for error in report["artifacts"]["errors"]:
        report_error("{error}", error=error)


def image_table(images):
    table([_("Image"), _("Allocation"), _("Assessment"), _("Protection")], [
        [item["path"], size(item.get("allocatedBytes")), item.get("classification", "unknown"),
         ", ".join(item.get("protectedBy", [])) or "—"] for item in images])


def operations(target):
    caps = capabilities(target)
    result = ["show", "export"]
    result += backup_operations(target)
    if security_policy(target)["mode"] != "none":
        result += ["unlock"] + (["prepare-image"] if target["backend"] != "rpi" else [])
    if caps["system"]:
        result += ["status", "doctor", "ssh", "verify", "update", "diff", "diffg"]
    if caps["hypervisor"] or target["backend"] == "rpi":
        result += ["cleanup", *target.get("commands", {})]
        if "provision" in target.get("commands", {}) or target["backend"] == "rpi":
            result += ["reproduce", "plan"]
        if target["backend"] == "rpi":
            result += ["probe"]
    return result


HELP_COMMANDS = "Everyday management:\n  list                         Local target inventory and available management capabilities.\n  show TARGET                  Target declaration, capacity, network and available operations.\n  status [TARGET]              Live status; hosts are queried concurrently and rows appear as ready.\n  ssh TARGET [-- COMMAND ...]  Log in or run a command on the server.\n  verify TARGET                Check the running system against the recipe, disk and services.\n  doctor [TARGET]              Diagnose one or all targets.\n  probe TARGET                 Detailed platform discovery for troubleshooting.\n\nUpdates and comparisons:\n  update TARGET                Update plan; --apply builds and activates the system.\n  diff TARGET                  Running system against the current recipe BEFORE an update.\n  diffg TARGET                 The two highest retained server generations.\n                               diff builds locally; diffg runs on the server.\n\nNew VM declarations (write a local file, do not create a VM):\n  new-libvirt-vm NAME          VM declaration for QEMU/KVM (libvirt).\n  new-hyperv-vm NAME           VM declaration for Hyper-V.\n  new-proxmox-vm NAME          Proxmox VE VM declaration with editable networking.\n  new-virtualbox-vm NAME       VM declaration for VirtualBox.\n  new-vmware-vm NAME           VM declaration for VMware Workstation.\n                               --host selects a host; a sole matching host is selected automatically.\n                               --offline prepares any backend without a hypervisor.\n                               --configuration NAME selects a server configuration; default is default.\n                               --dry-run does not write the file. new-* names without -vm are aliases.\n\nDeployment, recovery and offline transfer (subject to platform capabilities):\n  plan TARGET                  Read-only VM creation or RPi recovery plan.\n  reproduce TARGET             Complete deployment/recovery; execution requires --apply.\n  create TARGET                Create a VM; execution requires --apply.\n  install TARGET               Complete the selected deployment method; requires --apply.\n  provision TARGET             Create and install; execution requires --apply.\n  reset TARGET                 Remove an authorized VM on any of the five backends.\n                               Requires allowReset = true in the instance; without --apply, verifies only.\n  export TARGET --output DIR   Offline bundle with disk or ISO and recipe in a new directory.\n  cleanup [TARGET]             Preview cleanup candidates; deletes nothing.\n                               --artifact-root DIR adds image inspection.\n\nUsage and modes:\n  Without a command, list is shown. TARGET is a declaration name, not an IP address.\n  show TARGET lists the operations available for a particular target.\n  update/create/install/provision/reproduce/reset only plan without --apply.\n  new-* and export normally write locally; --dry-run shows a plan without writing.\n  diff/diffg may read the server and diff builds the recipe; --dry-run only shows the plan.\n  --ssh root@HOST:PORT overrides SSH access to an offline VM of any backend.\n  --method image is the default for all VMs: import a prebuilt disk and verify boot.\n  --method iso installs to a blank disk, detaches the ISO and verifies system boot.\n  Set deploymentMethod in a declaration for a persistent default; --method overrides it.\n  Applies to new-*, show, plan, create, install, provision, reproduce and export.\n  --offline prepares without a hypervisor, independently of the method. RPi uses SD.\n  diff shows pending changes. After an update it is usually empty; use diffg.\n\nExamples:\n  nixberry list\n  nixberry show vm-demo-libvirt\n  nixberry diff vm-demo-libvirt\n  nixberry update vm-demo-libvirt --apply\n  nixberry diffg vm-demo-libvirt\n  nixberry ssh vm-demo-libvirt -- uname -r\n  nixberry new-proxmox-vm vm-demo-proxmox --host HOST\n  nixberry plan vm-demo-proxmox\n  nixberry reproduce vm-demo-proxmox --apply\n  nixberry new-proxmox-vm vm-demo-proxmox-offline --offline\n  nixberry export vm-demo-proxmox-offline --output ~/vm-demo-proxmox-offline\n  nixberry update vm-demo-proxmox-offline --ssh root@10.0.1.19:22 --apply\n  nixberry plan rpi-demo --address 192.168.50.20\n\nDetailed operating guide: README.md in the NixBerry recipe root.\nComplete command and option reference: docs/cli.md.\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    remote_command = []
    if "--" in argv:
        delimiter = argv.index("--")
        argv, remote_command = argv[:delimiter], argv[delimiter + 1:]
    parser = ArgumentParser(
        prog="nixberry", description=_("Unified management of declared RPi and VMs: operations, deployment and offline export."),
        epilog=_(HELP_COMMANDS) + _(BACKUP_HELP) + "\n" + _("Libvirt reset allows directory symlinks; --apply requires interactive [y/N] confirmation for their resolved paths.")
        + "\n\n" + _("Encrypted VMs:\n  new-*-vm NAME --security luks creates a declaration for encrypted image delivery.\n  prepare-image TARGET prepares the disk outside the Nix store; prompts for a passphrase.\n  unlock TARGET --address IP connects to the pinned initrd SSH identity.\n  Set security.imageDirectory to a private directory outside the recipe.\n  Encrypted ISO installation and confidential launch are currently unavailable.\n  Confidential requests fail closed; no fallback to ordinary VMs is allowed.")
        + "\n\n" + _("Encrypted Raspberry Pi:\n  Set security.mode = luks in the physical target declaration.\n  Pin security.initrdHostKey and security.recoveryHostKey from trusted media preparation.\n  reproduce uses recovery media and --initrd-key-file; prompts for the LUKS passphrase.\n  unlock TARGET --address IP opens the pinned early-boot identity.\n  prepare-image applies only to VM disks. See docs/security.md."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="nixberry " + os.environ.get("NIXBERRY_VERSION", "development"))
    parser.add_argument("action", metavar=_("COMMAND"), help=_("Command from the list below (default: list)"), nargs="?", default="list",
                        type=lambda value: value + "-vm" if value in ("new-libvirt", "new-hyperv", "new-proxmox", "new-virtualbox", "new-vmware") else value, choices=(
        "list", "show", "status", "ssh", "probe", "plan", "create", "install", "provision", "reset",
        "new-libvirt-vm", "new-hyperv-vm", "new-proxmox-vm", "new-virtualbox-vm", "new-vmware-vm", "reproduce", "verify", "update", "diff", "diffg", "export", "doctor", "cleanup", "prepare-image", "unlock",
        "backup", "backups", "verify-backup", "restore", "recover-source", "backup-prune", "backup-schedule", "backup-check", "backup-monitor"))
    parser.add_argument("target", metavar=_("TARGET"), nargs="?", help=_("Existing target name, or a new name for new-*"))
    parser.add_argument("--host", help=_("Hypervisor host for new-*; direct SSH root@HOST[:PORT] for VM management"))
    parser.add_argument("--offline", action="store_true", help=_("Prepare any VM offline without a hypervisor; cannot be combined with --host"))
    parser.add_argument("--configuration", help=_("Server configuration for new-*; defaults to default, other names from configurations/"))
    parser.add_argument("--security", choices=("none", "luks", "confidential"), help=_("VM protection for new-*; confidential requires attested key delivery and is currently unavailable"))
    parser.add_argument("--key-file", help=_("Private runtime passphrase file for image preparation or encrypted RPi reproduction; otherwise prompt securely"))
    parser.add_argument("--initrd-key-file", help=_("Private runtime SSH host key for encrypted RPi reproduction"))
    parser.add_argument("--method", choices=("iso", "image"), help=_("VM deployment method: ISO installation or prebuilt disk import; overrides deploymentMethod in the declaration"))
    parser.add_argument("--ssh", help=_("One-time server SSH endpoint root@HOST[:PORT]; overrides access.host and access.port"))
    parser.add_argument("--output", help=_("New export, source recovery or backup schedule directory; relative paths use the working directory"))
    parser.add_argument("--snapshot", help=_("Explicit encrypted snapshot ID from backups TARGET"))
    parser.add_argument("--from", dest="backup_source", help=_("Declared backup source for restoration into a distinct target"))
    parser.add_argument("--address", help=_("Early-boot unlock address or current RPi recovery address"))
    parser.add_argument("--erase-existing", action="store_true", help=_("Include SSD erasure during RPi recovery"))
    parser.add_argument("--json", action="store_true", help=_("Machine-readable output for inventory, diagnostics, verify and export"))
    parser.add_argument("--artifact-root", action="append", default=[],
                        help=_("Image directory for cleanup preview; repeatable, links are not followed"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help=_("Apply changes for update and deployment/recovery operations"))
    modes.add_argument("--dry-run", action="store_true", help=_("Show the plan without writing; diff/diffg without connections or builds"))
    args = parser.parse_args(argv)
    if args.snapshot and args.action not in ("verify-backup", "restore", "recover-source"):
        parser.error(_("--snapshot only applies to verify-backup, restore and recover-source."))
    if args.backup_source and args.action != "restore":
        parser.error(_("--from only applies to restore TARGET --from SOURCE."))
    if args.security is not None and not args.action.startswith("new-"):
        parser.error(_("--security only applies to new-*-vm; edit security in existing declarations."))
    if args.key_file and args.action not in ("prepare-image", "plan", "reproduce"):
        parser.error(_("--key-file applies to prepare-image or encrypted RPi plan/reproduce."))
    if args.initrd_key_file and args.action not in ("plan", "reproduce"):
        parser.error(_("--initrd-key-file only applies to encrypted RPi plan/reproduce."))
    # Short names remain aliases; adapters keep the original backend identifier.
    if args.action.startswith("new-") and args.action.endswith("-vm"):
        args.action = args.action[:-3]
    if args.offline and (not args.action.startswith("new-") or args.host):
        parser.error(_("--offline applies to new-*-vm without --host."))
    if args.configuration is not None and not args.action.startswith("new-"):
        parser.error(_("--configuration only applies to new-*-vm; edit configuration in the declaration of an existing target."))
    if args.method and not (args.action.startswith("new-") or args.action in ("show", "plan", "create", "install", "provision", "reproduce", "export")):
        parser.error(_("--method applies to new-*, show, plan, create, install, provision, reproduce and export."))
    if remote_command and args.action != "ssh":
        parser.error(_("A command after -- is only available for ssh TARGET -- COMMAND."))
    registry = json.loads(Path(os.environ["NIXBERRY_DEPLOYMENT"]).read_text())
    if args.ssh and (args.host or args.action.startswith("new-")):
        parser.error(_("--ssh applies to server management and cannot be combined with --host or new-*."))
    override = args.ssh or (args.host if not args.action.startswith("new-") else None)
    if override:
        if args.action not in ("update", "ssh", "verify", "status", "doctor", "diff", "diffg"):
            parser.error(_("Direct SSH supports update, ssh, verify, status, doctor, diff and diffg."))
        if (args.target not in registry or registry[args.target]["backend"] not in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware")
                or not registry[args.target].get("offline")):
            parser.error(_("Direct SSH requires an offline VM declaration without hypervisor management."))
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
        if args.action in BACKUP_ACTIONS:
            parser.error(_("Backup operation unavailable: enable source backup policy and configure live access for capture or restore."))
        parser.error(_("Operation unavailable. Set access or use --ssh root@HOST:PORT for system management; hypervisor management requires a declared host."))
    if args.artifact_root and args.action != "cleanup":
        parser.error(_("--artifact-root only applies to cleanup."))
    if args.output and args.action not in ("export", "prepare-image", "recover-source", "backup-schedule", "backup-monitor"):
        parser.error(_("--output applies to export, prepare-image, recover-source, backup-schedule and backup-monitor."))
    if args.action.startswith("new-"):
        if args.apply or args.json or args.address or args.erase_existing:
            parser.error(_("new-* only creates a local declaration; it does not accept these options."))
        if not args.target:
            parser.error(_("Specify a new target name: {value1} NAME.").format(value1=args.action + "-vm"))
        hosts = json.loads(Path(os.environ["NIXBERRY_DEPLOYMENT_HOSTS"]).read_text())
        if args.action == "new-libvirt" and not args.offline:
            path, text = new_libvirt(os.environ.get("NIXBERRY_RECIPE", ""), hosts, registry,
                                    args.target, args.host, write=not args.dry_run, **({"method": args.method} if args.method else {}),
                                    **({"security_mode": args.security} if args.security else {}),
                                    **({"configuration": args.configuration} if args.configuration is not None else {}))
        else:
            path, text = new_vm(os.environ.get("NIXBERRY_RECIPE", ""), hosts, registry,
                                args.target, args.action[4:], args.host, write=not args.dry_run, offline=args.offline,
                                **({"security_mode": args.security} if args.security else {}),
                                **({"method": args.method} if args.method else {}),
                                **({"configuration": args.configuration} if args.configuration is not None else {}))
        if args.dry_run:
            info(_("Declaration plan: {path}"), path=path)
        else:
            success(_("Declaration created: {path}"), path=path)
            print(path)
        info(_("Server configuration: {configuration}"), configuration=args.configuration or "default")
        if args.dry_run:
            print(text)
        info(_("Before building, check network: DHCP, or mode = \"static\" with address, gateway and dns."))
        if args.offline:
            info(_("Offline declaration: edit capacity and deployment parameters in the file before export."))
            next_command = f"nixberry export {args.target} --output ~/{args.target}-offline"
        else:
            next_command = f"nixberry plan {args.target}"
        if args.security == "luks":
            info(_("Next step: {command}"), command=f"nixberry prepare-image {args.target}")
            info(_("After preparation: {command}"), command=next_command)
        else:
            info(_("Next step: {command}"), command=next_command)
        if not args.offline:
            info(_("After reviewing: {command}"), command=f"nixberry reproduce {args.target} --apply")
        return 0
    if (args.address or args.erase_existing) and not (args.action == "unlock" and not args.erase_existing) and not (
            args.action in ("plan", "reproduce") and args.target in registry
            and registry[args.target]["backend"] == "rpi"):
        parser.error(_("--address and --erase-existing only apply to plan/reproduce for RPi targets."))
    if args.json and args.action not in ("list", "show", "status", "verify", "export", "doctor", "cleanup", "prepare-image", *BACKUP_ACTIONS):
        parser.error(_("--json is available for list, show, status, verify, export, doctor and cleanup."))
    if args.action in ("list", "show", "status", "ssh", "probe", "plan", "verify", "export", "diff", "diffg", "doctor", "cleanup", "prepare-image", "unlock") and args.apply:
        parser.error(_("This operation does not accept --apply."))
    if args.action in ("backups", "verify-backup", "backup-schedule", "backup-monitor") and args.apply:
        parser.error(_("This operation does not accept --apply."))
    if args.action in ("doctor", "cleanup"):
        if args.target and args.target not in registry:
            parser.error(_("Unknown target. Available targets: {value1}").format(value1=", ".join(sorted(registry))))
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
                table([_("Target"), _("Check"), _("Result"), _("Details")], [
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
            parser.error(_("list does not accept a target or write mode."))
        if args.json:
            print(json.dumps({name: {key: value for key, value in target.items() if key != "commands"}
                              for name, target in registry.items()}, ensure_ascii=False, indent=2))
        else:
            if not registry:
                info(_("No targets are declared. Add a host and instance or an RPi target."))
                return 0
            table([_("Target"), _("Platform"), _("Configuration"), _("Build/export"), _("System management"), _("Hypervisor")], [
                [name, target["backend"], target.get("configuration", "default"), _("yes"), _("configured") if target["capabilities"]["system"] else _("unconfigured"),
                 target["host"] if target["capabilities"]["hypervisor"] else _("unmanaged")]
                for name, target in sorted(registry.items())])
            info(_("All target statuses: {command}"), command="nixberry status")
            info(_("Target details: {command}"), command="nixberry show TARGET")
        return 0
    if args.action == "status":
        if args.target and args.target not in registry:
            parser.error(_("Unknown target. Available targets: {value1}").format(value1=", ".join(sorted(registry))))
        names = [args.target] if args.target else sorted(registry)
        snapshots = collect_status(registry, names, status,
                                   on_result=None if args.json else status_printer(registry, names))
        if args.json:
            print(json.dumps(snapshots, ensure_ascii=False, indent=2))
        return int(any("error" in data for data in snapshots.values()))
    if args.target not in registry:
        parser.error(_("Select a target: {value1}. Arbitrary addresses are not allowed.").format(value1=", ".join(sorted(registry))))
    target = registry[args.target]
    if args.action in BACKUP_ACTIONS:
        if args.action == "recover-source" and not args.output:
            parser.error(_("recover-source requires --output NEW_DIRECTORY."))
        result = run_backup(os.environ.get("NIXBERRY_RECIPE", ""), registry, args.action, args.target,
                            source=args.backup_source, snapshot=args.snapshot, output=args.output,
                            apply=args.apply, dry_run=args.dry_run)
        if args.action == "backups" and not args.json and not args.dry_run:
            table([_("Snapshot"), _("Created (UTC)"), _("State")], [
                [row["snapshotId"], datetime.fromtimestamp(row["createdAt"], timezone.utc).strftime("%Y-%m-%d %H:%M"), row["state"]]
                for row in result.get("snapshots", [])])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(bool(result.get('alarm')) or result.get('notification', {}).get('state') == 'failed') if args.action == 'backup-check' else 0
    if (args.initrd_key_file or (args.key_file and args.action != "prepare-image")) and not (
            target["backend"] == "rpi" and security_policy(target)["mode"] == "luks"):
        parser.error(_("Runtime encryption keys require an encrypted RPi target."))
    if args.action in ("plan", "create", "install", "provision", "reproduce", "export", "prepare-image", "unlock", "update", "verify"):
        require_supported(target, args.method or deployment_method(target))
    if target["backend"] != "rpi" and args.action in ("plan", "create", "install", "provision", "reproduce", "export", "unlock") and security_policy(target)["mode"] == "luks":
        image_metadata(target, name=args.target)
    if args.action == "prepare-image":
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe or not (Path(recipe) / "flake.nix").is_file():
            parser.error(_("Run the operation through the nixberry launcher with a complete project recipe."))
        destination = args.output or security_policy(target).get("imageDirectory")
        if not destination:
            parser.error(_("Set security.imageDirectory or specify --output for prepare-image."))
        result = prepare_image(recipe, args.target, target,
                               destination, key_file=args.key_file, dry_run=args.dry_run)
        print(json.dumps(result, indent=2) if args.json or args.dry_run else result["destination"])
        return 0
    if args.action == "unlock":
        if not args.address:
            parser.error(_("Specify the early-boot VM IP address with unlock TARGET --address IP."))
        result = unlock(target, args.address, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(result, indent=2))
            return 0
        return result
    if args.method and target["backend"] == "rpi":
        parser.error(_("--method is for VMs; RPi uses a recovery SD card."))
    if args.action == "show" and args.method:
        target = {**target, "deploymentMethod": args.method}
    if args.action in ("diff", "diffg"):
        return compare(os.environ.get("NIXBERRY_RECIPE", ""), args.target, target,
                       generations=args.action == "diffg", dry_run=args.dry_run)
    if args.action == "reset" and target.get("allowReset", target["spec"].get("allowReset", False)) is not True:
        parser.error(_("Removal is not allowed. Set allowReset = true; in this VM's declaration in instances/, then review reset TARGET without --apply."))
    if args.action == "update":
        info(_("Server configuration: {configuration}"), configuration=target.get("configuration", "default"))
        update(os.environ.get("NIXBERRY_RECIPE", ""), args.target, target, apply=args.apply)
        return 0
    if args.action == "export":
        info(_("Server configuration: {configuration}"), configuration=target.get("configuration", "default"))
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe or not args.output:
            parser.error(_("Usage: nixberry export TARGET --output NEW_DIRECTORY."))
        result = export(recipe, args.target, target, args.output, dry_run=args.dry_run,
                        **({"method": args.method} if args.method else {}))
        if args.json or args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            success(_("Offline bundle ready: {path}"), path=result["destination"])
            print(result["destination"])
            info(_("Verify SHA256SUMS before transfer; instructions for the selected medium are in README.txt."))
        return 0
    if args.action == "verify":
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe:
            parser.error(_("Run verify through the nixberry launcher from the project root."))
        with materialized_recipe(recipe) as source:
            expected = read(["nix", "eval", "--raw", "--no-write-lock-file",
                             "path:" + source + "#nixberrySystems." + args.target + ".config.system.build.toplevel.outPath"])
        try:
            result = verify(target, expected)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            raise RuntimeError(_("Target {name} verification failed: {error}\nDiagnostics: {doctor}\nRecipe comparison: {diff}").format(
                                     name=args.target, error=error,
                                     doctor=f"nixberry doctor {args.target}{guest_host_option(target)}",
                                     diff=f"nixberry diff {args.target}{guest_host_option(target)}")) from error
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            success(_("Verified: {name}, {hostname}, system on {root}."),
                    name=args.target, hostname=result["hostname"], root=result["root"])
            print(result["system"])
        return 0
    if args.action in ("plan", "create", "install", "provision", "reproduce"):
        info(_("Server configuration: {configuration}"), configuration=target.get("configuration", "default"))
    if args.action in ("plan", "reproduce") and target["backend"] == "rpi":
        recipe = os.environ.get("NIXBERRY_RECIPE")
        if not recipe:
            parser.error(_("Run RPi reproduction through the nixberry launcher from the project root."))
        command = ["nix", "run", "--no-write-lock-file", "path:" + recipe + "#nixberry-rpi-install",
                   "--", "--target", args.target,
                   "--apply" if args.apply else "--dry-run"]
        if args.address:
            command += ["--address", args.address]
        if args.erase_existing:
            command.append("--erase-existing")
        if args.key_file:
            command += ["--key-file", args.key_file]
        if args.initrd_key_file:
            command += ["--initrd-key-file", args.initrd_key_file]
        with materialized_recipe(recipe) as source:
            command[3] = "path:" + source + "#nixberry-rpi-install"
            return subprocess.run(command, check=False).returncode
    if args.action == "show":
        if args.json:
            print(json.dumps({key: value for key, value in target.items() if key != "commands"},
                             ensure_ascii=False, indent=2))
        else:
            spec = target["spec"]
            caps = target["capabilities"]
            print(_("Target: {value1}\nPlatform: {value2}").format(value1=args.target, value2=target["backend"]))
            print(_("Configuration: {configuration}").format(configuration=target.get("configuration", "default")))
            print(_("Hypervisor: {value1}").format(value1=target["host"] if caps["hypervisor"] else _("unmanaged")))
            if target["backend"] != "rpi":
                print(_("Deployment method: {method}").format(method=args.method or deployment_method(target)))
                print(_("Security: {mode}; unlock: {unlock}").format(**security_policy(target)))
            print(_("Build/export: yes; system management: {value1}").format(value1=_("configured") if caps["system"] else _("unconfigured")))
            print(_("Platform profile: {value1}").format(value1=target["profile"]))
            if target["backend"] != "rpi":
                print(_("Removal allowed: {value}").format(value=_("yes") if target.get("allowReset", False) else _("no")))
            if target["backend"] == "rpi":
                print(_("RPi: {value1}\nAddress: {value2}").format(value1=spec["hostName"], value2=spec["address"]))
                print(_("Serial number: {value1}\nDisk: {value2}").format(value1=spec["hardwareSerial"], value2=spec["diskDevice"]))
            else:
                print(_('VM: {value1}').format(value1=spec["name"]))
                print(_("Resources: {value1} CPUs, {value2} MiB RAM, {value3} GiB disk").format(value1=spec["vcpus"], value2=spec["memoryMiB"], value3=spec["diskGiB"]))
                if target["backend"] == "proxmox":
                    print(_("Import: VMID {value1}, storage {value2}, bridge {value3}").format(value1=spec["vmid"], value2=spec["storage"], value3=spec["bridge"]))
                if target.get("offline"):
                    info(_("The operator creates the VM; prepare the image through offline export."))
                direct = direct_connection(target)
                if direct:
                    print(_("Server SSH: {value1}@{value2}:{value3}").format(value1=direct["user"], value2=direct["host"], value3=direct["port"]))
                    print(_("Key verification: strict known_hosts{value1}{value2}").format(value1=", soubor " + direct["knownHostsFile"] if direct.get("knownHostsFile") else "", value2=", alias " + direct["hostKeyAlias"] if direct.get("hostKeyAlias") else ""))
                network = target.get("network", {"mode": "dhcp"})
                if network.get("mode", "dhcp") == "static":
                    print(_("Network: {value1}, gateway {value2}, DNS {value3}").format(value1=network["address"], value2=network["gateway"], value3=", ".join(network["dns"])))
                else:
                    print(_("Network: DHCP"))
            print(_("Operation: {value1}").format(value1=", ".join(operations(target))))
            if not caps["system"]:
                info(_("Export: {command}"), command=f"nixberry export {args.target} --output ~/{args.target}-offline")
            else:
                info(_("Live status: {command}"), command="nixberry status " + args.target)
        return 0
    if args.action == "ssh":
        if args.dry_run:
            parser.error(_("ssh does not accept --dry-run; use status to check the state."))
        command = ssh_command(target, status(target))
        if not remote_command and sys.stdin.isatty():
            command[1:1] = ["-e", "~"]
            info(_("Ctrl+C interrupts a remote command. Disconnect a stuck session with Enter, then ~."))
        info(_("Connecting: {command}"), command=" ".join(command))
        try:
            return subprocess.run(command + remote_command, check=False).returncode
        except KeyboardInterrupt:
            return 130
    if args.action == "probe" and target["backend"] == "rpi":
        print(json.dumps(status(target), ensure_ascii=False, indent=2))
        return 0
    action = "provision" if args.action in ("plan", "reproduce") else args.action
    if action not in target["commands"]:
        parser.error(_("Operation {action} is not implemented for {value2}. Available: {value3}").format(action=action, value2=args.target, value3=", ".join(target["commands"])))
    command = [target["commands"][action]]
    if args.method:
        command += ["--method", args.method]
    if action not in ("probe", "reset") and target["backend"] != "rpi":
        info(_("Deployment method: {method}"), method=args.method or deployment_method(target))
    if action != "probe":
        command.append("--apply" if args.apply else "--dry-run")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
