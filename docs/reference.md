# Technical reference

[← Main guide](../README.md) · [CLI](cli.md) · [Testing](test-plan.md)

## Configuration boundaries

| Area | Source |
| --- | --- |
| Hosts and connections | Local `hosts.local.nix`, loaded through [hosts.nix](../hosts.nix) |
| Local VM declarations and loader | [instances/](../instances/default.nix) |
| Server configurations | [configurations/](../configurations/default.nix) |
| Shared base | [modules/common.nix](../modules/common.nix) |
| Services, packages, system settings | `modules/services/`, `modules/applications/`, `modules/system/` |
| Hardware and boot | `platforms/rpi/`, `platforms/vm/` |
| Physical RPi and SSD | [management/rpi/targets/](../management/rpi/targets/default.nix) |
| System, media and tool composition | [flake.nix](../flake.nix) |
| Registry and CLI | [management/deployment/](../management/deployment/default.nix) |
| Shared VM adapters | [portable.py](../management/deployment/portable.py), [host_agent.py](../management/deployment/host_agent.py) |

Use the selected configuration for application-specific services and `modules/`
for shared behavior. Platforms handle boot, disks, drivers and guest support;
they do not choose application configurations. Declarations contain identity,
networking and resources.

NixBerry has its own recipe and lock and works independently of notebook configuration.
Declaration directories track only loaders and ignore rules. Back up VM/RPi
declarations, `hosts.local.nix` and `admin.pub` separately. Use `path:` inputs to
include them without `git add`. Empty inventory is valid; an unknown host is an error.

## Named configurations

Every target selects one `configuration`, defaulting to `default`.
This applies to VM/RPi updates, images, ISOs and export.

For example, create `configurations/web.nix`:

```nix
{ ... }:
{
  imports = [ ../modules/common.nix ];
  services.nginx.enable = true;
}
```

Import the common base, not another configuration. Open firewall ports as needed.
The flake attaches the platform automatically.

```sh
nixberry new-libvirt-vm vm-demo-web --configuration web
nixberry show vm-demo-web
nixberry plan vm-demo-web
nixberry reproduce vm-demo-web --apply
```

Create this example `web` module before using it. For an existing target,
edit its stored `configuration` and continue with `diff` and `update`.
`--configuration` only applies when creating declarations.

Names start with a lowercase letter and contain lowercase letters, digits and hyphens.
Larger configurations may use `configurations/web/default.nix`; a simultaneous
`web.nix` is an error. Put helper modules inside the directory.
The catalogue ignores symlinks; listing it does not build systems:

```sh
nix eval --json path:.#nixberryConfigurations
```

Module content and ARM package availability are checked when evaluating/building
the selected system. Shared-base changes affect all configurations; a configuration
module affects only its users. Activation can restart services and change firewalls.
System rollback does not undo database migrations or application data changes.

### nixoncpp configuration

`configuration = "nixoncpp";` adds the console application
[NixonCpp](https://github.com/tomasmark79/NixonCpp) and its library.
The [module](../configurations/nixoncpp.nix) uses the upstream flake package
from its `nix` directory, pinned by the common lock.

```sh
NixonCpp --help
NixonCpp
```

It runs manually, creates no service and opens no ports.
`--write2file` writes `application.log` in the working directory.

## Identity and keys

A filename determines the CLI target name. VM hostname defaults to `spec.name`;
override with `guestHostName`. Another copy needs a new declaration, UUID and MAC.
Keep them for subsequent management of the same VM.

Prepare `admin.pub` before building media. A changed administrator key reaches a
running system only through `update`. Never put private keys in the recipe.
Remove hosts only after deleting or redirecting their declarations.

<a id="recept"></a>
<a id="recipe"></a>

## Immutable recipes and system generations

Deployment, export and updates use an immutable recipe snapshot.
If a declaration differs from the running CLI, rerun the operation;
the tool never silently switches targets. Temporary GC roots protect source
and built outputs throughout the operation.

Export includes this same snapshot as `recipe/`. It does not follow working-copy
changes and is not a copy of every Nix store dependency.
Create a new bundle for different settings.

`update --apply` verifies identity and disk installation, transfers the system
and activates it through `nixberry-update` with an explicit environment.
It then checks system and services. Interrupted SSH does not prove activation
stopped. `diffg` compares the highest retained generations, which need not be
the active and previously booted generations.

<a id="comparison-transfer"></a>

`diff` reads package paths, selected packages and closure sizes from the running
system over SSH and from the local build. Local `nvd` compares these metadata
snapshots through its `--nix-bin-dir` query interface. Its `running` and `recipe`
headers refer to temporary metadata directories; diagnostics show the actual
system paths. No running-system store paths or executable files are imported,
so unsigned builds do not require local daemon trust or signature exceptions.

The same procedure applies to all five VM backends and RPi, for installed image
and ISO systems and offline VM declarations managed through explicit guest SSH.
Target identity and installation are checked before reading metadata and the
active system is checked again before displaying the comparison. SSH requires
a known host key, including through jump hosts. The command does not write to
the target or activate a system. `diffg` continues to run `nvd` on the target.

<a id="diagnostika"></a>
<a id="diagnostics"></a>

## Diagnostics and storage

`doctor` checks identity, networking, services, blocks and inodes on `/`,
`/boot` and `/nix`. Usage at 90% is a warning; 98% is an error.
It does not build to confirm recipe consistency; `verify` does that.
See [commands and exit codes](cli.md#language-output-and-exit-codes).

`cleanup` is read-only. It protects the active, booted and default systems,
plus the highest retained generation different from the active one for rollback.
`review` means manual assessment; `unknown` means more information is needed.
Adding `closureBytes` does not estimate reclaimable space because dependencies are shared.

```sh
nixberry cleanup vm-demo-libvirt
nixberry cleanup --artifact-root /path/to/bundles --json
```

Inventory respects hypervisor and dedicated-storage boundaries.
Stopped VMware VMs outside that storage are not fully inventoried.
Thin-storage allocation may be unknown: `null` is not zero.
`--artifact-root` is repeatable; directory links are not followed and exports
with manifests are protected. Usage outside the inspected scope cannot be ruled out.

<a id="pamet"></a>
<a id="memory"></a>

## Memory and swap

The [swap module](../modules/system/swap.nix) uses zram with `zstd`,
capped at 2 GiB and at RAM size, priority 100.
Fallback `/swapfile` is 8 GiB at priority 10.

Check on the server:

```sh
free -h
grep -E '^(MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree):' /proc/meminfo
cat /proc/pressure/memory
swapon --show
zramctl
journalctl -b -u mkswap-swapfile.service
```

Creating a swap file on first boot can fill the filesystem cache, which a
hypervisor may count as used RAM. Distinguish `MemFree`, `Cached` and
`MemAvailable`. High cache with sufficient available memory and no pressure
does not itself prove RAM exhaustion.

## Shared applications

Midnight Commander uses the pinned `mc-modern` input.
Update it with `nix flake update mc-modern --flake .`, then update servers through
the normal CLI. User `~/.config/mc/ini` overrides package defaults.
The Bash `mc` wrapper preserves the last visited directory; the direct binary does not.
Wayland clipboard access requires a graphical session, not SSH alone.

[HSTR](../modules/applications/hstr/package.nix) opens history with `Ctrl+R` in
interactive Bash. Selecting inserts a command; Enter executes it.
History is synchronized before opening.

The pinned `nixos-raspberrypi` and [boot module](../platforms/rpi/boot.nix)
select RPi kernel/firmware, independently of application input `unstable`.

<a id="media"></a>

## Nix outputs without deployment

From the recipe root, for an existing local declaration:

```sh
nix build --no-link --print-out-paths --no-write-lock-file path:.#nixberrySystems.vm-demo-libvirt.config.system.build.toplevel
nix build --no-link --print-out-paths --no-write-lock-file path:.#nixberryImages.vm-demo-libvirt
nix build --no-link --print-out-paths --no-write-lock-file path:.#nixberryInstallers.vm-demo-libvirt
```

`nixberryImages.TARGET` selects a VM disk or full RPi SD.
`nixberryInstallers.TARGET` selects the VM ISO under `iso/*.iso`.

`nixberryTools.TARGET` honors the declared method.
`nixberryToolsByMethod.image.TARGET` and `.iso.TARGET` explicitly select a variant.
Each online VM backend provides `probe/create/install/provision/reset`.
Libvirt uses `nixberry-vm-*` and `nixberry-libvirt-probe`;
others use `nixberry-BACKEND-*`. Standalone `.reset` needs no system or media.
Manifests distinguish `imagePath` and `isoPath`; the unused field is `null`.

Generic images have 32 GiB and the profile's default hostname:

| Platform | Disk package | ISO package |
| --- | --- | --- |
| QEMU/KVM (libvirt) | `nixberry-qemu-image` | `nixberry-installer` |
| Hyper-V | `nixberry-hyperv-image` | `nixberry-hyperv-installer` |
| Proxmox VE | `nixberry-proxmox-image` | `nixberry-proxmox-installer` |
| VirtualBox | `nixberry-virtualbox-image` | `nixberry-virtualbox-installer` |
| VMware Workstation | `nixberry-vmware-image` | `nixberry-vmware-installer` |

RPi provides `nixberry-rpi-installer` and `nixberry-rpi-network-installer`.
Use `export` to hand off a particular machine with its matching medium,
declaration, lock and instructions.

## Testing and documentation maintenance

The [test guide](test-plan.md) separates isolated checks, builds, live deployment
and guest verification. Evidence from one layer does not prove another.

[VERSION](../VERSION) determines `nixberry --version`; it does not replace a
lockfile or exact system path.

[README](../README.md) is the entry guide, with English details in `docs/`.
Document current commands and limitations,
use `vm-demo-*` examples and read real inventory through `list`.
See [message conventions](messages.md) for English source strings and Czech catalogues.
