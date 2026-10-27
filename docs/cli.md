# CLI reference

[← Main guide](../README.md)

`nixberry [COMMAND] [TARGET] [OPTIONS]` works with local declarations.
Without a command it runs `list`. A target is a declaration name, not an IP.
A command after `--` is only accepted by `ssh`.

Encrypted VM declarations use `new-*-vm NAME --security luks`. The optional
`--security confidential` records a requirement that currently fails closed;
attested launch and key delivery are unavailable. Existing declarations are
edited directly. See [encryption and remote unlocking](security.md).

| Encryption command | Result |
| --- | --- |
| `prepare-image TARGET [--output DIR] [--key-file FILE]` | Prepare encrypted media outside the store; otherwise use `security.imageDirectory` and an interactive passphrase. |
| `prepare-image TARGET --dry-run` | Show the preparation plan without building or reading a passphrase. |
| `unlock TARGET --address IP` | Connect to the pinned early-boot SSH identity and enter the passphrase. |
| `unlock TARGET --address IP --dry-run` | Show the public unlock policy without connecting. |

Preparation accepts `--json`; unlocking is interactive. Neither command accepts
`--apply`. Encrypted ISO deployment is rejected; use `image`.

## Inventory and management

Encrypted application backups use `backup`, `backups`, `verify-backup`, `restore`,
`recover-source`, `backup-prune`, `backup-schedule`, `backup-check` and
`backup-monitor`. They require an explicit
target backup policy and a configuration-owned provider. See the complete
[backup commands and write boundaries](backups.md).

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
and do not reboot automatically. After interruption, inspect `nixberry-update`
and verify the server before retrying.

## New VM declarations

| Command | Backend |
| --- | --- |
| `new-libvirt-vm NAME` | QEMU/KVM (libvirt) |
| `new-hyperv-vm NAME` | Hyper-V |
| `new-proxmox-vm NAME` | Proxmox VE |
| `new-virtualbox-vm NAME` | VirtualBox |
| `new-vmware-vm NAME` | VMware Workstation |

Each command writes local `instances/NAME.nix`; it does not create a VM.
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
| `--ssh root@HOST[:PORT]` | Offline VM `status`, `ssh`, `doctor`, `verify`, `update`, `diff`, `diffg`. |
| `--host` | Hypervisor host for `new-*`; an alternative to `--ssh` for direct offline VM management. |
| `--output DIRECTORY` | `export` and VM `prepare-image`. |
| `--artifact-root DIRECTORY` | Only `cleanup`; repeatable. |
| `--address IP` | RPi `plan`/`reproduce`, or early-boot `unlock` for encrypted VMs and RPi. |
| `--erase-existing` | Only RPi `plan` and `reproduce`. |
| `--key-file FILE` | VM `prepare-image`, or encrypted RPi `plan`/`reproduce`; otherwise prompt on execution. |
| `--initrd-key-file FILE` | Encrypted RPi `plan`/`reproduce`; private SSH key matching `security.initrdHostKey`. |
| `--json` | `list`, `show`, `status`, `verify`, `doctor`, `cleanup`, `export`, `prepare-image`. |

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
Interpret nonzero codes together with the diagnostic and known operation state:
they do not imply previous writes were rolled back.
