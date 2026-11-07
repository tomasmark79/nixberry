# Offline bundles: disk, ISO or recovery SD

[← Main guide](../README.md) · [Platforms and hosts](vm.md)

A bundle supplies the system from a declaration, not the live server's data or
secrets. Preparation may download pinned dependencies; completed media includes
the target system. Applications may have their own network requirements.
Use your own declarations in place of `vm-demo-*` examples.

<a id="export"></a>

## Prepare a bundle

For an existing declaration, select one medium and a new destination:

```sh
nixberry export vm-demo-libvirt --output ~/vm-demo-libvirt-disk
nixberry export vm-demo-libvirt --method iso --output ~/vm-demo-libvirt-iso
nixberry export rpi-demo --output ~/rpi-demo-sd
```

VMs use `image` unless `deploymentMethod` says otherwise. `--method` overrides
it for this export. RPi exports a full recovery SD image.
`export … --dry-run` neither builds nor writes.
The destination must be outside the recipe; relative paths start at the working directory.

For someone else's hypervisor, create `new-*-vm NAME --offline`, set capacity
and networking, then export that target. No host access is needed.
Keep its identity for subsequent exports; a concurrent copy needs a separate
declaration and MAC.

| File | Purpose |
| --- | --- |
| `README.txt` | Instructions for the selected medium, in the selected CLI language. |
| `manifest.json` | Platform, method, capacity, networking and exact target system path. |
| `NETWORKS.txt` | Present for named VM adapters: ordered MAC/attachment bindings and per-interface addressing. |
| `SHA256SUMS` | Checksums of all files. |
| `recipe/` | The same recipe and lock used to build the medium, including local declarations. |
| `INSTALL-ISO.sh` | Guest-side check and installation, only in ISO bundles. |
| `PROXMOX.txt`, `VIRTUALBOX.txt` | Platform-specific prebuilt disk handoff. |

Review included local configuration before sharing. Before and after transfer,
run **from the bundle root**:

```sh
sha256sum -c SHA256SUMS
```

On Windows, compare `Get-FileHash -Algorithm SHA256 PATH` with the matching
`SHA256SUMS` entry. Each export is an independent snapshot. Working-copy
changes do not modify it; create a new bundle for a different configuration.

<a id="disk"></a>

## Boot a prebuilt system disk

Import **your own copy** of the image into a new VM. Do not attach an installation
ISO for `image`. Set CPU/RAM from the manifest, enable UEFI and disable Secure Boot.

| Platform | Image and controller | Guest service |
| --- | --- | --- |
| QEMU/KVM (libvirt) | QCOW2, VirtIO Block `/dev/vda` | `qemu-guest-agent`, with a communication channel |
| Hyper-V | VHDX, Generation 2, SCSI `/dev/sda` | `hv-kvp`, `hv-vss` |
| Proxmox VE | QCOW2, `virtio0` or VirtIO SCSI `scsi0` | QEMU Guest Agent enabled in VM configuration |
| VirtualBox | VDI, SATA | Guest Additions, `virtualbox` service |
| VMware Workstation | VMDK, SATA | VMware Tools, `vmware` service |

For another VDI copy, use `VBoxManage clonemedium disk` to get a unique media UUID.
Skip unattended installation when creating a VirtualBox VM and attach the existing disk.
Hyper-V assigns its own VM ID and BIOSGUID.

Connect the network to an environment matching manifest `network`.
DHCP requires a DHCP server; static IP requires the correct subnet and gateway.
Manual VirtualBox imports may use Bridged Adapter or NAT; configure SSH port
forwarding for NAT. The automatic adapter uses bridged networking.

After boot, verify from the VM console:

```sh
hostname
ip -4 address
ip route
resolvectl status
findmnt /
findmnt /boot
readlink -f /run/current-system
systemctl --failed
systemctl is-active sshd systemd-networkd
```

`/run/current-system` must match manifest `systemPath`. Also check the platform's
guest service and independently verify new SSH host keys before login.
Public HTTPS and NTP are not prerequisites for offline boot itself.

Manual import does not create ownership markers for automatic hypervisor management.
Matching UUID and MAC are not enough to adopt it. Use [direct SSH](vm.md#direct-ssh)
to manage the server.

<a id="iso"></a>

## Install from ISO

Use the ISO bundle for the **specific declaration and platform**.
Profiles have different disks and drivers; their media is not interchangeable.

1. Create a new VM with UEFI, disabled Secure Boot and one blank disk sized
   from the manifest. Use the controller and guest service above.
   For a Proxmox installation VM, use VirtIO Block `/dev/vda`.
2. Attach the ISO and boot `nixberry-installer`. On Proxmox, first upload it to
   storage allowing `iso` content and attach its managed volume; an arbitrary
   filesystem path is insufficient. Transfer `INSTALL-ISO.sh` over SSH or another medium.
3. Verify the correct VM in the hypervisor console, then read its DMI UUID with
   `cat /sys/class/dmi/id/product_uuid`.
4. Follow `README.txt` as root: run `dry-run` with the verified UUID, system,
   disko, capacity, profile and disk. After checks pass, repeat with `apply`.
5. After successful installation, shut down gracefully, detach ISO and boot from disk.
   Verify networking, services and exact `systemPath` as for image import.

The ISO includes the prepared system and disko; installation does not use network
substituters. The script rejects incorrect identity, profile or capacity and disks
with partitions or signatures. If installation fails, keep the ISO attached and
inspect state first. Writing again to a partially installed disk is not allowed.

Managed VMs perform these steps automatically through
`nixberry reproduce TARGET --method iso --apply`, including host checks.

## Proxmox handoff example

```sh
nixberry new-proxmox-vm vm-demo-proxmox --offline
```

Set the operator-assigned VMID, resources and network in the declaration, then:

```sh
nixberry export vm-demo-proxmox --output ~/vm-demo-proxmox-disk
```

Follow the generated `PROXMOX.txt`: import a QCOW2 copy, use UEFI/OVMF with
Secure Boot disabled, enable QEMU Guest Agent and attach the correct bridge.
The operator provides storage, VLANs/firewall and an available VMID.

Partitions mount by labels, so the prebuilt image supports VirtIO Block
`/dev/vda` or VirtIO SCSI `/dev/sda`. A larger disk does not enlarge partitions.
Named adapter images require every declared MAC/attachment in `NETWORKS.txt`;
legacy single-adapter imports can use an operator-assigned MAC. Set `access`
for later system management; it changes neither the image network nor VM ownership.

<a id="rpi"></a>

## RPi recovery SD

The full `img.zst` supplies an environment and target system for a specific RPi
and SSD. Follow [SD recovery](rpi-recovery.md). The smaller network SD requires
target store transfer; use the full image for local recovery without internet.

Booting media offline does not mean new manager-side builds can run without
prepared dependencies. The source recipe and lock are not copies of the Nix store.
The full SD includes the components needed for its local installer.

## Build media directly

Prefer `export TARGET` for normal handoff: it selects the system and media and
includes instructions. See [technical outputs](reference.md#media) for
`nixberryImages.TARGET` and `nixberryInstallers.TARGET`.
Generic images use a default profile and hostname; they do not replace an
instance-specific export.
