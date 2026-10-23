# NixBerry

<p align="center">
  <img src="assets/NixBerryLogo.svg" alt="NixBerry logo" width="240">
</p>

<p align="center">
  <a href="https://matrix.to/#/#nixberry:matrix.org">Matrix Channel</a>
</p>

NixBerry builds and manages NixOS servers on **QEMU/KVM (libvirt), Hyper-V,
Proxmox VE, VirtualBox, VMware Workstation and Raspberry Pi 5**. One recipe
defines the system; each machine declaration selects its configuration,
platform, network and resources.

VMs default to a prebuilt system disk (`image`). Every VM backend also
supports installation from ISO. Raspberry Pi uses a recovery SD card.

## Get started

NixBerry is in active development. The [platform guide](docs/vm.md) describes
supported environments; the [test guide](docs/test-plan.md) distinguishes
isolated checks, builds and live deployment evidence.

The management computer needs Linux x86_64, Nix with flakes enabled, and SSH.
After cloning, enter the project root and add it to your shell's `PATH`:

```sh
export PATH="$PWD:$PATH"
nixberry --help
```

Prepare your public SSH key and host connections using the
[host templates](management/examples/hosts.nix):

```sh
cp ~/.ssh/id_ed25519.pub admin.pub
cp management/examples/hosts.nix hosts.local.nix
```

Keep only your hosts in `hosts.local.nix` and adjust their parameters.
Use `local-libvirt` for a first local QEMU/KVM VM.
The hypervisor, network and storage must already exist. Offline preparation
needs no hypervisor connection, but still needs the administrator's public key.
A fresh clone has no declared targets.

```sh
nixberry list
nixberry --help
```

A **target** is a declared server; a **host** runs its hypervisor.
`list` shows existing targets. [hosts.nix](hosts.nix) loads your local host file.
All `vm-demo-*` names in this guide are examples; use your own target names.

English is the default regardless of your system locale. To use Czech:

```sh
NIXBERRY_LANG=cs nixberry --help
# Or persist the choice in your shell:
export NIXBERRY_LANG=cs
```

Use `NIXBERRY_LANG=en` for English. JSON keys, technical values and external
tool output do not change language. Documentation is maintained in English.

| Task | Guide |
| --- | --- |
| Check status, log in or update | [Everyday management](#management) |
| Deploy a VM from disk or ISO | [New VM](#deployment) |
| Hand off a system without hypervisor access | [Offline handoff](#offline) |
| Change packages, services or declarations | [Configuration](#configuration) |
| Recover Raspberry Pi | [SD recovery](docs/rpi-recovery.md) |
| Look up commands and options | [CLI reference](docs/cli.md) |
| Troubleshoot a failure | [Troubleshooting](#troubleshooting) |

<a id="sprava"></a>
<a id="management"></a>

## Everyday management

```sh
nixberry show vm-demo-libvirt
nixberry status vm-demo-libvirt
nixberry ssh vm-demo-libvirt
nixberry doctor vm-demo-libvirt
nixberry verify vm-demo-libvirt
```

`show` reads the declaration and lists available operations. `status` reads
live state, querying hosts concurrently when no target is given.
Specify one target for a quick check. `doctor` diagnoses operational issues;
`verify` also checks the running system against the recipe.

<a id="aktualizace"></a>
<a id="porovnani-zmen"></a>
<a id="updates"></a>

### Update a system

Edit the recipe or declaration, then:

```sh
nixberry diff vm-demo-libvirt
nixberry update vm-demo-libvirt
nixberry update vm-demo-libvirt --apply
nixberry verify vm-demo-libvirt
nixberry diffg vm-demo-libvirt
```

- `diff` compares the running system with the current recipe and may build locally.
  It reads package metadata over verified SSH without importing the running
  system into the local Nix store; see [comparison metadata](docs/reference.md#comparison-transfer).
- `update` without `--apply` shows a plan without connecting or building.
- `update --apply` builds, transfers and activates the system. It does not resize the VM or repartition disks.
- `diffg` compares the two highest retained server generations. After an update,
  `diff` is usually empty; use `diffg` to inspect the generation change.

The same workflow applies to VMs and RPi. Activation may restart services,
but does not automatically reboot the machine. Update dependencies separately
with `nix flake update --flake .` in this recipe's root; server updates use
its current lock. See [generations and immutable snapshots](docs/reference.md#recipe).

<a id="reprodukce"></a>
<a id="deployment"></a>

## New VM

Declare an existing hypervisor, network and storage in `hosts.local.nix`,
loaded through [hosts.nix](hosts.nix). NixBerry does not create those resources.

```sh
nixberry new-libvirt-vm vm-demo-libvirt --host local-libvirt
nixberry show vm-demo-libvirt
```

This writes `instances/vm-demo-libvirt.nix`. Review its configuration,
network and capacity. Defaults are 4 CPUs, 4 GiB RAM and a 32 GiB disk.
Keep the UUID and MAC when managing the same VM.

```sh
nixberry plan vm-demo-libvirt
nixberry reproduce vm-demo-libvirt --apply
nixberry verify vm-demo-libvirt
```

| Platform | Declaration command |
| --- | --- |
| QEMU/KVM (libvirt) | `new-libvirt-vm` |
| Hyper-V | `new-hyperv-vm` |
| Proxmox VE | `new-proxmox-vm` |
| VirtualBox | `new-virtualbox-vm` |
| VMware Workstation | `new-vmware-vm` |

Without `--host`, a sole matching host is selected automatically.
Select explicitly if several exist. `new-* --dry-run` only prints the proposal.
A Proxmox VMID unused in local inventory may still be occupied in the cluster;
`plan` checks that live.

<a id="metody"></a>
<a id="methods"></a>

### Disk or ISO

| Method | `create TARGET --apply` | `install TARGET --apply` |
| --- | --- | --- |
| `image` — default | Creates a powered-off VM with a prebuilt system disk. | Starts it and verifies the system from disk. |
| `iso` | Creates a blank disk and boots the installation ISO. | Installs, shuts down gracefully, detaches ISO and verifies boot from disk. |

`reproduce --apply` and `provision --apply` combine both steps.
`deploymentMethod` stores the default; `--method image|iso` overrides it once:

```sh
nixberry new-proxmox-vm vm-demo-proxmox --host HOST --method iso
nixberry plan vm-demo-proxmox
nixberry reproduce vm-demo-proxmox --apply
```

Use the same method for separate `create` and `install` operations.
Deployment refuses existing targets; the ISO installer also refuses disks
with partitions or signatures. After a failure, inspect `status` and `probe`
before deciding how to continue. See [deployment and reset](docs/vm.md).

<a id="offline"></a>

## Offline handoff

When a colleague manages the hypervisor, prepare a declaration without a host:

```sh
nixberry new-proxmox-vm vm-demo-proxmox-offline --offline
```

Set capacity and network in the declaration. DHCP is the default; for an
assigned static address:

```nix
network = {
  mode = "static";
  address = "10.0.1.19/24";
  gateway = "10.0.1.1";
  dns = [ "10.0.1.1" ];
};
```

Static addresses use IPv4 CIDR notation with a prefix from 0 to 32 and an IPv4
gateway. DNS entries must be IPv4 or IPv6 literals.

These addresses are examples. The hypervisor operator provides the bridge,
VLAN, firewall and connection to the correct subnet. Export a disk or ISO:

```sh
nixberry export vm-demo-proxmox-offline --output ~/vm-demo-proxmox-disk
nixberry export vm-demo-proxmox-offline --method iso --output ~/vm-demo-proxmox-iso
```

The destination must be new and outside the recipe. The bundle contains media,
a manifest, the recipe with local declarations, checksums and operator instructions.
Building may require internet; completed media includes the target system.
**Export is not a backup of server data or secrets.** Review included local
configuration before sharing a bundle.

After import, manage the server through `access` in its declaration or a one-time endpoint:

```sh
nixberry doctor vm-demo-proxmox-offline --ssh root@10.0.1.19:22
nixberry update vm-demo-proxmox-offline --ssh root@10.0.1.19:22 --apply
```

SSH access does not grant hypervisor management.
See [disk, ISO and Proxmox handoff](docs/offline.md).

<a id="obnova-rpi"></a>

## Raspberry Pi 5

RPi declarations live in `management/rpi/targets/`. Everyday management uses
the same `status`, `ssh`, `diff`, `update` and `verify` commands.
A full recovery uses SD media:

```sh
nixberry export rpi-demo --output ~/rpi-demo-sd
```

Follow [RPi SD recovery](docs/rpi-recovery.md). Booting the SD alone erases nothing.
Writing the SSD requires explicit execution and verified RPi and disk identity.
VM `--method` selection and hypervisor reset do not apply.

<a id="konfigurace"></a>
<a id="configuration"></a>

## Configuration and local declarations

| Setting | Location |
| --- | --- |
| Hypervisor connections and resources | Local `hosts.local.nix`, loaded by [hosts.nix](hosts.nix) |
| A VM's identity, network and capacity | `instances/NAME.nix` |
| RPi, SSD and network | [management/rpi/targets/](management/rpi/targets/default.nix) |
| Packages and services | [configurations/](configurations/default.nix) |
| Shared system base | [modules/common.nix](modules/common.nix) and `modules/` |
| Boot, disks, drivers and guest support | `platforms/` |
| Administrator public key | Local `admin.pub` |

Each target selects one `configuration`, defaulting to `default`.
Use `--configuration` when creating a declaration:

```sh
nixberry new-libvirt-vm vm-demo-nixoncpp --configuration nixoncpp
```

For an existing machine, edit its declaration and continue with `diff` and `update`.
See [custom configurations, applications and builds](docs/reference.md).

**Concrete VM and RPi declarations, `hosts.local.nix` and `admin.pub` are not tracked in Git.**
They work without `git add`; back them up separately. Declaration directories
only track their loader and ignore rules. Restore or create local data after cloning.
NixBerry maintains its own `flake.lock`.

<a id="potize"></a>
<a id="troubleshooting"></a>

## Troubleshooting

| Situation | Next step |
| --- | --- |
| Inventory takes too long | Use `status TARGET` rather than querying all hosts. |
| VM is running but the server does not respond | `doctor TARGET`, `probe TARGET` and the hypervisor console. |
| Creation reports an existing VM | Inspect state; `install` may be the next step for its method. |
| `diff` is empty after an update | Use `diffg TARGET`. |
| SSH interrupted an update | Check server `journalctl -u nixberry-update`, then run `verify`. |
| Assess disk usage | `cleanup TARGET` previews findings without deleting. |
| Remove a managed VM | [Protected reset](docs/vm.md#reset): review the plan and set `allowReset = true;`. |
| libvirt storage uses directory symlinks | Reset shows resolved disk/NVRAM paths; `--apply` requires interactive `[y/N]` confirmation. Links replacing the files themselves remain unsupported. See [protected reset](docs/vm.md#reset). |
| libvirt reset reports `qemu-img` permission denied | The local account needs read access to verify the QCOW2. Reset stops without changing permissions or elevating privileges. |
| Hypervisor reports nearly full RAM | Check cache, `MemAvailable` and memory pressure; [details](docs/reference.md#memory). |

Disconnect a stuck interactive SSH session with **Enter, `~`, `.`**.
Independently verify a changed SSH host key; old keys are not removed automatically.
See [SSH and guest agents](docs/vm.md#direct-ssh).

## Shell and detailed guides

Bash completes commands, targets, hosts, configurations and options.
Load it in your current shell from the recipe root:

```sh
source ./completion.bash
```

The launcher and completion honor exported `NIXBERRY_RECIPE`; otherwise they
use the project directory. Invalid explicit paths fail without switching copies.
NixBerry works independently of Nixon.

| Guide | Contents |
| --- | --- |
| [CLI](docs/cli.md) | Commands, options and write modes. |
| [VM platforms](docs/vm.md) | Hosts, direct SSH, guest agents and reset. |
| [Offline handoff](docs/offline.md) | Disk import, ISO installation and Proxmox handoff. |
| [RPi recovery](docs/rpi-recovery.md) | SD preparation and SSD recovery. |
| [Technical reference](docs/reference.md) | Modules, configurations, applications and Nix outputs. |
| [Testing](docs/test-plan.md) | Isolated checks and live test matrix. |
| [Platform consistency](docs/platform-consistency.md) | Shared contracts and review rules. |
| [Messages](docs/messages.md) | Languages, streams, colors and translation maintenance. |
| [Proxmox lab](docs/proxmox-lab.md) | Nested host for integration testing. |

## License

Author: Tomáš Mark. NixBerry source code is available under the [MIT license](LICENSE).
Dependencies and external tools retain their own licenses.
