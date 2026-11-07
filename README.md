# NixBerry

<p align="center">
  <img src="assets/NixBerryLogo.svg" alt="NixBerry logo" width="240">
</p>

NixBerry builds and manages NixOS systems on **Raspberry Pi 5**, **QEMU/KVM
(libvirt), Hyper-V, Proxmox VE, VirtualBox and VMware Workstation**. A local
machine declaration selects its configuration, hardware, network and resources.
The same locked recipe supplies updates and installation media.

## Start with a Raspberry Pi 5

The management computer needs x86_64 Linux, Nix with flakes, and SSH. From a
checkout, add the launcher to your shell and prepare your public administrator key:

```sh
export PATH="$PWD:$PATH"
test -e admin.pub || cp ~/.ssh/id_ed25519.pub admin.pub
nixberry --help
```

Keep an existing `admin.pub` if it already contains the intended key. Never copy
private keys into the recipe. A fresh clone has no declared machines.

1. Copy [examples/rpi.nix](examples/rpi.nix) to `machines/rpi/rpi-demo.nix`.
   Replace its board serial, adapter MAC, SSD identity, addresses and capacity
   with your own values. The filename determines the CLI target name.
2. Follow [RPi installation and recovery](docs/rpi-recovery.md): prepare an
   attached SSD directly on Linux, or boot recovery SD media on the RPi.
   Direct preparation requires ARM binfmt emulation and an unencrypted target;
   [LUKS installation](docs/security.md#raspberry-pi-5) uses recovery media.
3. Boot from the SSD and run `nixberry verify rpi-demo`.
   Building or writing media alone does not verify physical boot.

Disk installation requires an explicit `--apply` and verified disk identity;
`--erase-existing` additionally permits erasing its existing contents. Installation
recreates the system, while [application recovery](docs/backups.md) restores data
separately. Keep a usable recovery SD and an independent backup.

For a first two-port router with DHCP and AdGuard Home, use the
[playground quick start](docs/playground.md). For a gamepad console, see
[retro gaming](docs/retrogaming.md). These are optional named configurations.

<a id="management"></a>

## Everyday management

Replace `rpi-demo` with your own target. The same commands manage installed VMs:

```sh
nixberry list
nixberry show rpi-demo
nixberry status rpi-demo
nixberry ssh rpi-demo
nixberry doctor rpi-demo
nixberry verify rpi-demo
```

`show` reads the declaration; `status` reads live state. `doctor` diagnoses
operational problems, and `verify` also checks the system against the recipe.
Use a specific target for a quick check; unqualified `status` queries all hosts.

<a id="updates"></a>

### Update

Edit the selected configuration or declaration, then:

```sh
nixberry diff rpi-demo
nixberry update rpi-demo
nixberry update rpi-demo --apply
nixberry verify rpi-demo
nixberry diffg rpi-demo
```

`update` alone shows a plan without connecting or building. `--apply` builds,
transfers and activates the system; services may restart. It does not resize
partitions or reboot automatically. `diff` compares the recipe with the running
system and may build locally; `diffg` compares the two highest retained server
generations. See [update options](docs/cli.md#updates) and
[RPi network changes](docs/rpi-recovery.md#updating-network-declarations).
Update dependencies separately with `nix flake update --flake .`.

<a id="backups"></a>

### Back up and recover

The selected configuration declares application data, writers and encrypted
Restic storage. Complete the [backup setup](docs/backups.md#configuration-owned-restic-uploads)
once, then use:

```sh
nixberry backup rpi-demo
nixberry backup rpi-demo --apply --json
nixberry ssh rpi-demo -- nixberry-restic backups --json
nixberry backup-check rpi-demo --json
nixberry ssh rpi-demo -- nixberry-restic verify-backup --snapshot SNAPSHOT_ID --json
```

Use a completed backup's `snapshotId`. Freshness checks require a configured
`maxAgeSeconds`; full verification authenticates the payload but does not test
application restoration. Source timers can run without the management computer.

A local Btrfs snapshot is temporary state on the source disk. A completed Restic
backup is encrypted application data and its recovery recipe on separate storage.
Ext4 uses cold capture, keeping declared writers stopped during upload. Neither
an exported image nor a Git commit backs up live application data.

Keep storage credentials, repository pins and independent recovery access outside
the source. The [backup guide](docs/backups.md) covers scheduling, retention,
source loss and restoration into a distinct prepared replacement. The selected
configuration's own guide supplies its service-specific recovery procedure.

<a id="configuration"></a>

## Configuration and local files

| Change | Location |
| --- | --- |
| Which RPi/SSD or VM, its identity and network | `machines/rpi/NAME.nix` or `machines/vm/NAME.nix` |
| Packages and services | The module selected by `configuration` under `configurations/` |
| Hypervisor connections | Local `hosts.local.nix`, loaded by [hosts.nix](hosts.nix) |
| Administrator public key | Local `admin.pub` |

Each target selects one configuration, defaulting to `default`. For an existing
machine, edit that field and use `diff` and `update`. The
[technical reference](docs/reference.md#named-configurations) explains custom and
linked configurations, shared modules and platform boundaries.

**Concrete declarations, `hosts.local.nix` and `admin.pub` are ignored by Git.**
Back them up separately and restore them after cloning. Launchers include them
without `git add`. NixBerry has its own `flake.lock` and works independently of
Nixon. Exported recipes can include local configuration: review them before sharing.

<a id="deployment"></a>

## Virtual machines

Prepare an existing hypervisor, network and storage using the
[host templates](examples/hosts.nix) and [VM guide](docs/vm.md). Then, for example:

```sh
nixberry new-libvirt-vm vm-demo-libvirt --host local-libvirt
nixberry show vm-demo-libvirt
nixberry plan vm-demo-libvirt
nixberry reproduce vm-demo-libvirt --apply
nixberry verify vm-demo-libvirt
```

Review the generated declaration before deployment. Each backend has its own
`new-*-vm` command. `image` is the default; `--method iso` selects installation
onto a verified blank disk. [VM deployment](docs/vm.md#deploy-a-new-vm) explains
both methods. Existing VMs are not taken over or reinstalled automatically.

For a hypervisor managed by someone else, create a declaration with `--offline`
and [export a disk or ISO bundle](docs/offline.md). Direct SSH manages its installed
system, without granting hypervisor control. RPi uses physical recovery, with no
VM method selection or reset operation.

<a id="troubleshooting"></a>

## Troubleshooting and command help

| Situation | Next step |
| --- | --- |
| Server does not respond | `doctor TARGET`; inspect the physical or hypervisor console. |
| SSH was interrupted during update | Inspect `journalctl -u nixberry-update` on the server, then run `verify`. |
| SSH host key changed | Independently verify the fingerprint; old keys are not removed automatically. |
| Need to inspect disk use | `cleanup TARGET` previews findings without deleting. |
| Need to remove a managed VM | Follow [protected reset](docs/vm.md#reset). |

Disconnect a stuck interactive SSH session with **Enter, `~`, `.`**.
Use `nixberry --help` and the [CLI reference](docs/cli.md) for commands and write
boundaries. English is the default; `NIXBERRY_LANG=cs` selects Czech without
changing commands, JSON keys or external-tool output.

Load Bash completion with `source ./completion.bash`. Launcher and completion use
exported `NIXBERRY_RECIPE`, otherwise their project directory. Invalid explicit
paths fail without switching recipes.

| Detailed guide | Purpose |
| --- | --- |
| [RPi recovery](docs/rpi-recovery.md) | Direct SSD preparation, SD installation and physical networking. |
| [Backups](docs/backups.md) | Application coverage, storage, schedules and independent recovery. |
| [VMs](docs/vm.md) · [VM networking](docs/vm-networking.md) | Hosts, deployment, adapters, SSH and removal. |
| [Offline bundles](docs/offline.md) · [Encryption](docs/security.md) | Handoff and manual LUKS unlock. |
| [Technical reference](docs/reference.md) | Configuration ownership, Nix outputs and system details. |
| [Testing](docs/test-plan.md) · [Platform contract](docs/platform-consistency.md) | Checks, supported variants and evidence boundaries. |
| [Messages](docs/messages.md) | Translation and output conventions for contributors. |

Author: Tomáš Mark. Source: [MIT license](LICENSE).
Dependencies and external tools retain their own licenses.
[Matrix channel](https://matrix.to/#/#nixberry:matrix.org).
