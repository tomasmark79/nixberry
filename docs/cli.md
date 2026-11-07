# CLI reference

[← Main guide](../README.md)

`nixberry [COMMAND] [TARGET] [OPTIONS]` works with local declarations.
Without a command it runs `list`. A target is a declaration name, not an IP.
A command after `--` is only accepted by `ssh`.

Encrypted VM declarations use `new-*-vm NAME --security luks`. The optional
`--security confidential` records a requirement that currently fails closed;
attested launch and key delivery are unavailable. Existing declarations are
edited directly. See [encryption and remote unlocking](security.md).

New VM declarations accept `--filesystem ext4|btrfs`; ext4 remains the default.
For example, `nixberry new-libvirt-vm vm-demo-server --offline --filesystem btrfs`
creates a declaration with `rootFilesystem = "btrfs";`. The same field is available
in RPi declarations. It selects the root layout for images and installation,
including roots inside LUKS; it does not convert an installed filesystem.
`update --apply` refuses a root filesystem that differs from the declaration.
Btrfs uses `@root` mounted at `/`, with separate `@snapshots` at `/.snapshots`
and `@swap` at `/swap` so active swap is outside root snapshots. Existing ext4
layouts keep `/swapfile`. Disk format selection alone enables no backup jobs.

| Encryption command | Result |
| --- | --- |
| `prepare-image TARGET [--output DIR] [--key-file FILE]` | Prepare encrypted media outside the store; otherwise use `security.imageDirectory` and an interactive passphrase. |
| `prepare-image TARGET --dry-run` | Show the preparation plan without building or reading a passphrase. |
| `unlock TARGET --address IP` | Connect to the pinned early-boot SSH identity and enter the passphrase. |
| `unlock TARGET --address IP --dry-run` | Show the public unlock policy without connecting. |

Preparation accepts `--json`; unlocking is interactive. Neither command accepts
`--apply`. Encrypted ISO deployment is rejected; use `image`.

## Inventory and management

`snapshot TARGET`, `snapshots TARGET`, `snapshot-remove TARGET --snapshot ID`
and `snapshot-resume TARGET` use the selected configuration's application
catalogue. Creation, removal and writer recovery require `--apply`; otherwise
they show a plan without contacting the source. Inventory reads a verified
running source unless `--dry-run` is selected. These commands require a Linux
controller and a running installed system with the expected generation and
policy. Local snapshot IDs have 32 hexadecimal characters.

`backup TARGET --apply` dispatches to an enabled target-level legacy provider,
or otherwise to `nixberry.backup.restic` in the selected NixOS configuration.
Native capture requires separately provisioned source credentials and a staged
recovery archive. `backup-stage TARGET` prepares a reviewable selected recipe;
`--apply` verifies the source and transfers/binds it under the capture lock.
The controller checks that the selected archive reproduces the expected system
and policy without changing locks or importing derivation results. NixBerry
updates refresh the archive automatically after verified activation; updates
through other tools and rollbacks require explicit staging before backup.
`--ssh` can supply access to an offline
VM for native capture, staging and maintenance.

`backup-prune TARGET` uses configuration-owned `restic.keepLast` when no enabled
legacy provider is declared. Its default reads the actual plan from the verified
source and repository; `--dry-run` contacts neither. `--apply` revalidates the
generation and current machine identity under the capture lock, authenticates
metadata and verifies the newest retained payload and sealed recipe before
deleting explicit old receipt/payload IDs. Duplicate receipts share a retention
group; other machines and incomplete captures remain untouched.

`backup-check TARGET` uses configured `restic.maxAgeSeconds` for the current
source machine. Native checks are read-only and reject `--apply`. They authenticate
metadata and the selected sealed recipe, with full payload verification remaining
separate. Status is `ok`, `stale`, `missing` or `future`; return code is `0` for
healthy and `1` otherwise. Source check service/timer failures provide systemd
status and journal diagnostics without desktop notifications.

`backups`, `verify-backup`, `backup-check` and `recover-source` accept an explicit
private `--backup-access FILE` for native storage reads without contacting the source
or requiring its declaration. Independent `backup-check` also requires
`--max-age-seconds N` (1–31536000); it works after source loss and uses the newest
authenticated capture for the preserved source identity. Source recovery writes
only with `--apply` into a new `--output` directory; it never evaluates recovered code. Without an access
context, inventory, verification and source recovery use an enabled legacy provider.
`backup-schedule` and `backup-monitor` require that provider policy. Legacy
`backup-check --apply` retains its protected history and desktop notifications;
legacy scheduling and monitoring exports remain controller user units.
Native guest inventory and verification remain available.

Prepared destinations can enable `nixberry.backup.restore.prepare` in their
selected configuration. The installed `nixberry-restore-target status --json`
inspects held writers and application paths without changing data or services.
`restore TARGET --from SOURCE --backup-access FILE --snapshot RECEIPT_ID --apply`
publishes authenticated application data into a distinct prepared destination;
without `--apply` it only plans. Enabled source backup, prune and check jobs/timers
are held until restoration completes. Independent reads and restore use Restic
`--no-lock`; the storage operator must coordinate them with pruning because
read-only SFTP access provides no reader lease. See the destination preparation
section in the [backup guide](backups.md#prepared-restore-destinations).
See [backup commands and write boundaries](backups.md).

`backup-schedule TARGET --output NEW_DIRECTORY --dry-run` previews systemd user
units for the controller running NixBerry. It requires `backup.enable = true`
and `backup.schedule` in that target's declaration. Without `--dry-run`, it
writes the unit files locally; it does not install or enable them.
It does not configure a timer on the target. If the server already schedules
its own backups, for example through NixOS Restic, edit that configuration
instead of adding a second capture scheduler.

| Command | Result |
| --- | --- |
| `list` | Local targets, configurations and management capabilities. |
| `show TARGET` | Declaration, network, resources, method and available operations. |
| `status [TARGET]` | Fresh state of one or all targets, without a cache. |
| `ssh TARGET [-- COMMAND …]` | Interactive login or a remote command. |
| `probe TARGET` | Detailed host/platform discovery; RPi has its own probe. |
| `doctor [TARGET]` | Operational diagnostics without building the target system. |
| `verify TARGET` | Identity, installation, services and recipe consistency checks. |
| `cleanup [TARGET]` | Preview generations, VMs and images; deletes nothing. |

Without a target, `status`, `doctor` and `cleanup` cover the whole inventory.
`list`, `show` and read-only probes do not need system or media builds.
`verify` evaluates the expected system. The tools themselves may be built
on first use.

## Updates

| Command | Result |
| --- | --- |
| `diff TARGET` | Compare the running system with the recipe; may build locally and retrieve system paths. |
| `diffg TARGET` | Compare the two highest retained server generations on the server. |
| `update TARGET` | Show a plan without connecting or building. |
| `update TARGET --apply` | Build, transfer, activate and verify the system. |

`diff` and `diffg` do not change the server. Their `--dry-run` skips connections
and builds. System comparison does not compare application data. After a rollback,
the highest generation may not be active; `diffg` reports the active system separately.

Updates use the current `flake.lock`, do not update dependencies or partitions,
and do not reboot automatically. Activation takes the capture locks of both
generations and refuses pending writer recovery. Native Restic configurations
prepare recovery code before transfer and stage it after verified activation.
A staging failure reports that the update was verified and provides a retry
command; stale recovery code cannot be used for a native capture. After
interruption, inspect `nixberry-update` and verify the server before retrying.

## New VM declarations

| Command | Backend |
| --- | --- |
| `new-libvirt-vm NAME` | QEMU/KVM (libvirt) |
| `new-hyperv-vm NAME` | Hyper-V |
| `new-proxmox-vm NAME` | Proxmox VE |
| `new-virtualbox-vm NAME` | VirtualBox |
| `new-vmware-vm NAME` | VMware Workstation |

Each command writes local `machines/vm/NAME.nix`; it does not create a VM.
Names contain 1–40 lowercase letters, digits and hyphens, start with a letter,
and end with a letter or digit. Existing declarations are not overwritten.

- `--host HOST` selects a local `hosts.local.nix` entry through `hosts.nix`; a sole matching host is selected automatically.
- `--offline` creates a declaration without a hypervisor and cannot be combined with `--host`.
- `--configuration NAME` selects a module from `configurations/`, defaulting to `default`.
- `--method image|iso` stores the deployment method, defaulting to `image`.
- `--dry-run` prints the proposal without writing a file.

`new-libvirt`, `new-hyperv`, `new-proxmox`, `new-virtualbox` and `new-vmware`
are aliases of their `-vm` commands.

## Deployment and removal

| Command | Result |
| --- | --- |
| `plan TARGET` | VM creation or RPi recovery plan. |
| `create TARGET --apply` | Create a new VM using the selected method. |
| `install TARGET --apply` | Complete an existing VM deployment using the same method. |
| `provision TARGET --apply` | Create and complete a VM. |
| `reproduce TARGET --apply` | Complete VM deployment or RPi recovery. |
| `reset TARGET --apply` | Remove an authorized managed VM after safety checks. |

Without `--apply`, these operations only plan. Preparing tools and media may
still build or download dependencies. `plan` does not accept `--apply`.

`image` creates a powered-off VM with a prebuilt disk; `install` starts and
verifies it. `iso` creates a blank disk and boots the installer; `install`
installs, shuts down gracefully, detaches ISO and verifies boot from disk.

`reset` requires instance `allowReset = true;` and a declared host.
Without `--apply`, it only verifies and needs no system or media.
Offline VMs and RPi do not support it. See [removal scope](vm.md#reset).

## Export

```sh
nixberry export vm-demo-libvirt --output ~/vm-demo-libvirt-disk
nixberry export vm-demo-libvirt --method iso --output ~/vm-demo-libvirt-iso
nixberry export rpi-demo --output ~/rpi-demo-sd
```

`export` normally builds and writes a local bundle. `--dry-run` builds and
writes nothing; `--apply` does not apply. The directory must be new and outside
the recipe. Relative paths start at the current working directory.
See [bundle contents and handoff](offline.md).

## Shared options

| Option | Applicability |
| --- | --- |
| `--help`, `--version` | Tool help and version. |
| `--method image|iso` | VM `new-*`, `show`, `plan`, `create`, `install`, `provision`, `reproduce`, `export`. |
| `--configuration NAME` | Only `new-*`; edit the declaration for an existing target. |
| `--offline` | Only `new-*` without `--host`. |
| `--ssh root@HOST[:PORT]` | Offline VM system management, native snapshots/backup maintenance and prepared restore; legacy providers use declared access. |
| `--host` | Hypervisor host for `new-*`; an alternative to `--ssh` for direct offline VM management. |
| `--output DIRECTORY` | `export`, VM `prepare-image`, source recovery and legacy timer exports. |
| `--backup-access FILE` | Private native context for storage inventory, verification, freshness, source recovery or prepared destination restore. |
| `--max-age-seconds N` | Required explicit threshold for `backup-check --backup-access`, from 1 through 31536000. |
| `--snapshot ID` | Explicit backup receipt for verification/recovery/restore, or local capture ID for `snapshot-remove`. |
| `--from SOURCE` | Source identity for `restore TARGET`; native access also requires a full receipt ID. |
| `--artifact-root DIRECTORY` | Only `cleanup`; repeatable. |
| `--address IP` | RPi `plan`/`reproduce`, or early-boot `unlock` for encrypted VMs and RPi. |
| `--local-disk /dev/disk/by-id/ID` | RPi `plan`/`reproduce`: prepare an attached SSD on Linux without contacting the board. Unencrypted targets only; incompatible with `--address` and runtime keys. |
| `--erase-existing` | Only RPi `plan` and `reproduce`. |
| `--key-file FILE` | VM `prepare-image`, or encrypted RPi `plan`/`reproduce`; otherwise prompt on execution. |
| `--initrd-key-file FILE` | Encrypted RPi `plan`/`reproduce`; private SSH key matching `security.initrdHostKey`. |
| `--json` | `list`, `show`, `status`, `verify`, `doctor`, `cleanup`, `export`, `prepare-image`, and all snapshot and backup commands described above. |

`--apply` and `--dry-run` are mutually exclusive. For `doctor` and `cleanup`,
`--dry-run` does not skip read-only queries. `ssh` and `list` do not accept it.
`--ssh` and `--host` cannot be combined. Direct SSH does not change image networking.

## Language, output and exit codes

English is the default independently of `LANG` or `LC_ALL`.
Set `NIXBERRY_LANG=cs` for Czech, or `NIXBERRY_LANG=en` for English.
Unknown language codes and missing translations fall back to English.
This environment setting is shared by the CLI and direct adapters.
Pure Nix evaluation errors and generated declaration comments remain English.

Data and help go to stdout; progress and diagnostics go to stderr.
`--json` remains valid JSON without progress text. Keys, schemas, state codes
and technical values are stable across languages; human message fields can
be translated. Colors follow the destination stream, `NO_COLOR` and
`TERM=dumb`. External tool output is not translated.

`doctor` returns `0` for no findings, `1` for errors and `2` for warnings only.
Incomplete `cleanup` inventory returns `1` and retains partial results.
Native `backup-check` returns `0` only for `status = ok`; unhealthy findings and
errors return `1`. Legacy checks retain their alarm and notification exit behavior.
Interpret nonzero codes together with the diagnostic and known operation state:
they do not imply previous writes were rolled back.
