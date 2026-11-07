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

## Snapshots and backups

| Command | Result |
| --- | --- |
| `snapshot TARGET`, `snapshots TARGET` | Plan/create a local Btrfs capture, or read recorded local captures. |
| `snapshot-remove TARGET --snapshot ID` | Plan removal of a local capture. |
| `snapshot-resume TARGET` | Plan interrupted writer recovery. |
| `backup TARGET` | Plan capture through an enabled provider, otherwise configuration-owned Restic. |
| `backup-stage TARGET` | Prepare a selected recovery recipe; applying verifies/transfers/binds it, or delegates to a provider. |
| `backups TARGET`, `verify-backup TARGET --snapshot ID` | List completed backups or fully authenticate one. Restic storage reads require `--backup-access`; installed guest tools are also available. |
| `backup-prune TARGET` | Read an actual retention plan; `--dry-run` reads local policy only. |
| `backup-check TARGET` | Check freshness; Restic is read-only, providers can record history/notify with `--apply`. |
| `recover-source TARGET --snapshot ID --output DIR` | Plan recovery of the authenticated recipe into a new directory. |
| `restore TARGET --from SOURCE --snapshot ID` | Plan application restoration into a distinct prepared destination. |
| `backup-schedule TARGET --output DIR`, `backup-monitor TARGET --output DIR` | Export provider controller user units into a new directory; install/enable nothing. |

Creation, removal, staging, source recovery and data restoration require
`--apply` for execution. Restic source commands need Linux, verified root SSH,
the expected running generation and installed policy. Local captures use
32-character IDs; Restic restore points use full 64-character receipt IDs.

`--backup-access FILE` selects independent Restic inventory, verification,
freshness, source recovery or prepared destination restore. Storage reads need
neither the source machine nor its declaration; independent freshness additionally
requires `--max-age-seconds N`. Provider reads use their declared source policy.
`--dry-run` skips storage/provider execution. Timer exports write locally by
default, without `--apply`; `--dry-run` previews them.

The [backup guide](backups.md) owns configuration, credential setup, schedules,
retention, writer recovery and destination requirements. Inventory and freshness
are not full payload verification; verification is not an application restore
test. Restoration neither installs a machine nor activates recovered code.

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
generations and refuses pending writer recovery. Restic configurations
prepare recovery code before transfer and stage it after verified activation.
A staging failure reports that the update was verified and provides a retry
command; stale recovery code cannot be used for a Restic backup. After
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
| `--ssh root@HOST[:PORT]` | Offline VM system management, local Btrfs snapshots, Restic backup maintenance and prepared restore; providers use declared access. |
| `--host` | Hypervisor host for `new-*`; an alternative to `--ssh` for direct offline VM management. |
| `--output DIRECTORY` | `export`, VM `prepare-image`, source recovery and provider timer exports. |
| `--backup-access FILE` | Private Restic context for storage inventory, verification, freshness, source recovery or prepared destination restore. |
| `--max-age-seconds N` | Required explicit threshold for `backup-check --backup-access`, from 1 through 31536000. |
| `--snapshot ID` | Explicit backup receipt for verification/recovery/restore, or local capture ID for `snapshot-remove`. |
| `--from SOURCE` | Source identity for `restore TARGET`; Restic access also requires a full receipt ID. |
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
Restic `backup-check` returns `0` only for `status = ok`; unhealthy findings and
errors return `1`. Provider checks retain their alarm and notification exit behavior.
Interpret nonzero codes together with the diagnostic and known operation state:
they do not imply previous writes were rolled back.
