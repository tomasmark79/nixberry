"""Argument definitions and user help for the NixBerry CLI."""

import argparse
import os

from messages import _, ArgumentParser

COMMANDS = (
    "list",
    "show",
    "status",
    "ssh",
    "probe",
    "plan",
    "create",
    "install",
    "provision",
    "reset",
    "new-libvirt-vm",
    "new-hyperv-vm",
    "new-proxmox-vm",
    "new-virtualbox-vm",
    "new-vmware-vm",
    "reproduce",
    "verify",
    "update",
    "diff",
    "diffg",
    "export",
    "doctor",
    "cleanup",
    "prepare-image",
    "unlock",
    "backup",
    "backups",
    "verify-backup",
    "restore",
    "recover-source",
    "backup-prune",
    "backup-schedule",
    "backup-check",
    "backup-monitor",
    "backup-stage",
)

HELP_COMMANDS = """Everyday management:
  list                         Local target inventory and available management capabilities.
  show TARGET                  Target declaration, capacity, network and available operations.
  status [TARGET]              Live status; hosts are queried concurrently and rows appear as ready.
  ssh TARGET [-- COMMAND ...]  Log in or run a command on the server.
  verify TARGET                Check the running system against the recipe, disk and services.
  doctor [TARGET]              Diagnose one or all targets.
  probe TARGET                 Detailed platform discovery for troubleshooting.

Updates and comparisons:
  update TARGET                Update plan; --apply builds and activates the system.
  diff TARGET                  Running system against the current recipe BEFORE an update.
  diffg TARGET                 The two highest retained server generations.
                               diff builds locally; diffg runs on the server.

New VM declarations (write a local file, do not create a VM):
  new-libvirt-vm NAME          VM declaration for QEMU/KVM (libvirt).
  new-hyperv-vm NAME           VM declaration for Hyper-V.
  new-proxmox-vm NAME          Proxmox VE VM declaration with editable networking.
  new-virtualbox-vm NAME       VM declaration for VirtualBox.
  new-vmware-vm NAME           VM declaration for VMware Workstation.
                               --host selects a host; a sole matching host is selected automatically.
                               --offline prepares any backend without a hypervisor.
                               --configuration NAME selects a server configuration; default is default.
                               --dry-run does not write the file. new-* names without -vm are aliases.

Deployment, recovery and offline transfer (subject to platform capabilities):
  plan TARGET                  Read-only VM creation or RPi recovery plan.
  reproduce TARGET             Complete deployment/recovery; execution requires --apply.
  create TARGET                Create a VM; execution requires --apply.
  install TARGET               Complete the selected deployment method; requires --apply.
  provision TARGET             Create and install; execution requires --apply.
  reset TARGET                 Remove an authorized VM on any of the five backends.
                               Requires allowReset = true in the instance; without --apply, verifies only.
  export TARGET --output DIR   Offline bundle with disk or ISO and recipe in a new directory.
  cleanup [TARGET]             Preview cleanup candidates; deletes nothing.
                               --artifact-root DIR adds image inspection.

Usage and modes:
  Without a command, list is shown. TARGET is a declaration name, not an IP address.
  show TARGET lists the operations available for a particular target.
  update/create/install/provision/reproduce/reset only plan without --apply.
  new-* and export normally write locally; --dry-run shows a plan without writing.
  diff/diffg may read the server and diff builds the recipe; --dry-run only shows the plan.
  --ssh root@HOST:PORT overrides SSH access to an offline VM of any backend.
  --method image is the default for all VMs: import a prebuilt disk and verify boot.
  --method iso installs to a blank disk, detaches the ISO and verifies system boot.
  Set deploymentMethod in a declaration for a persistent default; --method overrides it.
  Applies to new-*, show, plan, create, install, provision, reproduce and export.
  --offline prepares without a hypervisor, independently of the method. RPi uses SD.
  diff shows pending changes. After an update it is usually empty; use diffg.

Examples:
  nixberry list
  nixberry show vm-demo-libvirt
  nixberry diff vm-demo-libvirt
  nixberry update vm-demo-libvirt --apply
  nixberry diffg vm-demo-libvirt
  nixberry ssh vm-demo-libvirt -- uname -r
  nixberry new-proxmox-vm vm-demo-proxmox --host HOST
  nixberry plan vm-demo-proxmox
  nixberry reproduce vm-demo-proxmox --apply
  nixberry new-proxmox-vm vm-demo-proxmox-offline --offline
  nixberry export vm-demo-proxmox-offline --output ~/vm-demo-proxmox-offline
  nixberry update vm-demo-proxmox-offline --ssh root@10.0.1.19:22 --apply
  nixberry plan rpi-demo --address 192.168.50.20

Detailed operating guide: README.md in the NixBerry recipe root.
Complete command and option reference: docs/cli.md.
"""

BACKUP_HELP = """
Encrypted backup commands:
  backup TARGET          Review coordinated capture; --apply creates an encrypted backup.
  backups TARGET         List recorded snapshots without contacting the source.
  verify-backup TARGET   Fully authenticate --snapshot ID without contacting the source.
  restore TARGET         Plan data recovery with --from SOURCE --snapshot ID; --apply publishes data.
  recover-source TARGET  Recover configuration with --snapshot ID --output DIR --apply.
  backup-prune TARGET    Preview keepLast per machine identity; --apply removes verified old copies.
  backup-schedule TARGET Export controller-side user units with --output DIR; never enables them.
  backup-check TARGET    Check backup freshness and run history; --apply records and notifies.
  backup-monitor TARGET  Export monitoring-only user units with --output DIR; never enables them.
  backup-stage TARGET    Review source-owned backup provisioning; --apply refreshes runtime access and recovery code.

Controller-side scheduling:
  nixberry backup-schedule TARGET --output NEW_DIRECTORY --dry-run
  Requires backup.enable = true and backup.schedule in the target declaration.
  Without --dry-run, writes user service/timer files on this controller, not on TARGET.
  It does not install or enable units and does not change a server-owned timer.
  If the server already runs scheduled backups (for example Restic), manage that
  schedule in its NixOS configuration; keep one capture scheduler.

Backup policy lives in the target declaration. Keys remain runtime files outside Git and the Nix store.
Restore does not create a VM, activate configuration or reboot. See docs/backups.md.
"""


ENCRYPTED_RPI_HELP = """Encrypted Raspberry Pi:
  Set security.mode = luks in the physical target declaration.
  Pin security.initrdHostKey and security.recoveryHostKey from trusted media preparation.
  reproduce uses recovery media and --initrd-key-file; prompts for the LUKS passphrase.
  unlock TARGET [--address IP] opens the pinned early-boot identity.
  Named networks use security.interface or network.primary; legacy targets require --address.
  prepare-image applies only to VM disks. See docs/security.md."""

ENCRYPTED_VM_HELP = """Encrypted VMs:
  new-*-vm NAME --security luks creates a declaration for encrypted image delivery.
  prepare-image TARGET prepares the disk outside the Nix store; prompts for a passphrase.
  unlock TARGET --address IP connects to the pinned initrd SSH identity.
  Set security.imageDirectory to a private directory outside the recipe.
  Encrypted ISO installation and confidential launch are currently unavailable.
  Confidential requests fail closed; no fallback to ordinary VMs is allowed."""

LOCAL_SSD_HELP = """Local Raspberry Pi SSD installation:
  plan/reproduce TARGET --local-disk /dev/disk/by-id/ID prepares an attached SSD on Linux.
  Unencrypted targets only; requires sudo and ARM binfmt emulation on x86.
  --apply authorizes writes; --erase-existing additionally authorizes erasing existing data.
  No RPi connection is made. Verify physical boot and restore application state separately."""


def create_parser():
    """Create a localized parser without reading inventory or running commands."""
    parser = ArgumentParser(
        prog="nixberry",
        description=_(
            "Unified management of declared RPi and VMs: operations, deployment and offline export."
        ),
        epilog=_(HELP_COMMANDS)
        + _(BACKUP_HELP)
        + "\n"
        + _(
            "Libvirt reset allows directory symlinks; --apply requires interactive [y/N] confirmation for their resolved paths."
        )
        + "\n\n"
        + _(LOCAL_SSD_HELP)
        + "\n\n"
        + _(ENCRYPTED_VM_HELP)
        + "\n\n"
        + _(ENCRYPTED_RPI_HELP),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version="nixberry " + os.environ.get("NIXBERRY_VERSION", "development"),
    )
    parser.add_argument(
        "action",
        metavar=_("COMMAND"),
        help=_("Command from the list below (default: list)"),
        nargs="?",
        default="list",
        type=lambda value: (
            value + "-vm"
            if value
            in (
                "new-libvirt",
                "new-hyperv",
                "new-proxmox",
                "new-virtualbox",
                "new-vmware",
            )
            else value
        ),
        choices=COMMANDS,
    )
    parser.add_argument(
        "target",
        metavar=_("TARGET"),
        nargs="?",
        help=_("Existing target name, or a new name for new-*"),
    )
    parser.add_argument(
        "--host",
        help=_(
            "Hypervisor host for new-*; direct SSH root@HOST[:PORT] for VM management"
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=_(
            "Prepare any VM offline without a hypervisor; cannot be combined with --host"
        ),
    )
    parser.add_argument(
        "--configuration",
        help=_(
            "Server configuration for new-*; defaults to default, other names from configurations/"
        ),
    )
    parser.add_argument(
        "--security",
        choices=("none", "luks", "confidential"),
        help=_(
            "VM protection for new-*; confidential requires attested key delivery and is currently unavailable"
        ),
    )
    parser.add_argument(
        "--key-file",
        help=_(
            "Private runtime passphrase file for image preparation or encrypted RPi reproduction; otherwise prompt securely"
        ),
    )
    parser.add_argument(
        "--initrd-key-file",
        help=_("Private runtime SSH host key for encrypted RPi reproduction"),
    )
    parser.add_argument(
        "--method",
        choices=("iso", "image"),
        help=_(
            "VM deployment method: ISO installation or prebuilt disk import; overrides deploymentMethod in the declaration"
        ),
    )
    parser.add_argument(
        "--ssh",
        help=_(
            "One-time server SSH endpoint root@HOST[:PORT]; overrides access.host and access.port"
        ),
    )
    parser.add_argument(
        "--output",
        help=_(
            "New export, source recovery or backup schedule directory; relative paths use the working directory"
        ),
    )
    parser.add_argument(
        "--snapshot", help=_("Explicit encrypted snapshot ID from backups TARGET")
    )
    parser.add_argument(
        "--from",
        dest="backup_source",
        help=_("Declared backup source for restoration into a distinct target"),
    )
    parser.add_argument(
        "--address", help=_("Early-boot unlock address or current RPi recovery address")
    )
    parser.add_argument(
        "--local-disk",
        help=_(
            "Install an RPi SSD attached to this Linux workstation; use a whole-disk /dev/disk/by-id/ path"
        ),
    )
    parser.add_argument(
        "--erase-existing",
        action="store_true",
        help=_("Include SSD erasure during RPi recovery"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=_("Machine-readable output for inventory, diagnostics, verify and export"),
    )
    parser.add_argument(
        "--artifact-root",
        action="append",
        default=[],
        help=_(
            "Image directory for cleanup preview; repeatable, links are not followed"
        ),
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--apply",
        action="store_true",
        help=_("Apply changes for update and deployment/recovery operations"),
    )
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help=_(
            "Show the plan without writing; diff/diffg without connections or builds"
        ),
    )
    return parser
