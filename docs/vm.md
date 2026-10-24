# VM platforms, hosts and SSH

[← Main guide](../README.md) · [CLI](cli.md) · [Offline handoff](offline.md)

<a id="hostitele"></a>
<a id="podpora"></a>
<a id="hosts"></a>

## Prepare a host

Declare connections and existing resources in local `hosts.local.nix`.
[hosts.nix](../hosts.nix) loads it, or returns an empty inventory when absent.
[Host templates](../management/examples/hosts.nix) cover all five hypervisors.
Adding an entry does not install a hypervisor or create networks or storage.

| Platform (backend) | Host environment | System disk and network |
| --- | --- | --- |
| QEMU/KVM (libvirt), `libvirt` | Local Linux, KVM, `qemu:///system` | QCOW2, VirtIO Block, declared NAT network with DHCP. |
| Hyper-V, `hyperv` | Windows with Hyper-V and SSH | VHDX, Generation 2, SCSI, declared switch. |
| Proxmox VE, `proxmox` | Proxmox VE, SSH to the declared node | VirtIO disk, existing bridge, QEMU Guest Agent. |
| VirtualBox, `virtualbox` | Local Linux or Linux/Windows over SSH | VDI, SATA, existing bridged adapter, Guest Additions. |
| VMware Workstation, `vmware` | Local Linux or Linux/Windows over SSH | monolithicSparse VMDK, SATA, NAT with DHCP, VMware Tools. |

All VMs use UEFI with Secure Boot disabled. Adapters manage new owned VMs;
they do not take over existing foreign machines. Libvirt supports local URIs.
The VMware adapter targets Workstation, not ESXi, vCenter or Fusion.

Automatic host access uses private IPv4. Address `192.168.79.1` is protected
and cannot be a managed target or jump host.

### QEMU/KVM (libvirt)

QEMU/KVM is the virtualization platform; NixBerry manages it through libvirt.
The CLI and declarations use backend identifier `libvirt`.

Prepare KVM, access to `qemu:///system`, an active storage pool, a DHCP network,
sufficient space and a supported machine type. The host connection specifies
these parameters; the recipe supplies OVMF and deployment media.

`image` imports QCOW2 into a new volume. `iso` creates a blank `/dev/vda` disk.
Guest access uses a DHCP lease from the declared network.

### Hyper-V

The SSH account needs Hyper-V management permission and write access to dedicated
storage. Prepare OpenSSH, a verified host key and TCP forwarding for jump access.
The connection declares `windowsComputerName`, `switchName`, `switchId` and storage.

VMs use Generation 2, disabled Secure Boot and fixed RAM capacity.
Hyper-V assigns actual VM IDs and BIOSGUIDs; the adapter verifies their link
to the owner record. Guest SSH may pass through the Windows host.
The guest services are `hv-kvp` and `hv-vss`.

### Proxmox VE

The host needs Python 3, `qm`, `pvesh`, permissions and existing
`storageDirectory`, `storage`, `bridge` and `node`. SSH must connect to the
declared node. Enable QEMU Guest Agent and its communication channel in the VM.

`image` imports a prebuilt disk. `iso` also needs storage with `iso` content,
selected by `isoStorage` (default `local`). The installation ISO is attached
as a managed volume, not an arbitrary filesystem path. After installation and
shutdown, the adapter detaches and removes its own media copy.
Plans check staging, disk and EFI volume space; thin overcommit is not enabled
automatically. A [nested Proxmox lab](proxmox-lab.md) is available for integration tests.

### VirtualBox

Prepare Python 3, working `VBoxManage`, the same user's VM registry and the
declared bridged adapter:

```sh
VBoxManage --version
VBoxManage list bridgedifs
```

NixOS needs its VirtualBox host module and user permissions. On Windows, use
the same account for SSH and GUI. The adapter grants that account's SID access
only to the newly created private VM directory.

Hardware virtualization must be available to VirtualBox. On Linux,
`kvm.enable_virt_at_load=Y` can reserve it while KVM modules are loaded, even
without running KVM VMs. Coordinate hypervisor access in the host configuration.
On Windows with Hyper-V, also verify that the installed VirtualBox UEFI firmware
can boot the guest. A CPU exception in UEFI must be resolved on the host before
Linux or [remote disk unlocking](security.md) can start.

### VMware Workstation

Prepare Python 3, working `vmrun`, dedicated storage and NAT with DHCP.
The `iso` method also needs `vmware-vdiskmanager` beside `vmrun`.
Use the same SSH and GUI account on Windows. VMware host modules and NAT/DHCP
must work on NixOS.

`toolPath` may be a full path containing spaces. `vmrun` manages the declared
VMX path; this does not guarantee its appearance in Workstation's GUI inventory.
Open the owned VMX directly in Workstation if needed.

### Shared portable adapter requirements

Proxmox, VirtualBox and VMware use Python selected by `pythonCommand`.
On Windows, verify it from the SSH session; a Microsoft Store alias is insufficient.
`hostName` must match `socket.gethostname()`.

`storageDirectory` must exist and be writable. The adapter only creates a new
VM subdirectory and refuses drive roots, redirected paths and foreign content.
Nix runs on the manager, not the remote host. Remote SSH requires a verified
host key and forwarding when guest access uses a jump host.

<a id="prime-ssh"></a>
<a id="direct-ssh"></a>

## Manage a server without its hypervisor

An offline VM declaration can enable direct system management. Add beside `spec`:

```nix
access = {
  host = "server.example";
  port = 22;
  user = "root";
  knownHostsFile = null;
  hostKeyAlias = null;
};
```

This enables `status`, `ssh`, `doctor`, `verify`, `diff`, `diffg` and `update`.
Use `--ssh` for a one-time endpoint:

```sh
nixberry doctor vm-demo-proxmox --ssh root@server.example:2030
```

Neither `access` nor `--ssh` changes image IPs, system configuration or VM ownership.
Only the `root` account is supported. These commands also accept
`--host root@HOST:PORT` as an alternative; for `new-*`, `--host` selects a hypervisor.

A custom `knownHostsFile` must be absolute; `null` uses the standard SSH file.
For a custom port, store its verified key as `[HOST]:PORT`.
`hostKeyAlias` supplies a stable identity when endpoints change.
An address change never accepts a new key automatically, and one-time SSH
preserves declared key verification.

Without SSH configuration, offline targets offer `show` and `export`;
`status` and `doctor` explain missing access. Direct SSH cannot create or reset VMs.
For automatic creation of a new VM, replace `backend` with a declared `host`
and remove `access`; this does not adopt an existing manual import.

## SSH and guest agent troubleshooting

The administrator key comes from `admin.pub`. Automatic deployment stores the
verified installed-system host key at
`~/.local/state/nixberry/portable/UUID/known_hosts`, honoring `XDG_STATE_HOME`.
Installation environments use temporary files. Hyper-V also keeps a stable alias
across IP changes.

For `HOST IDENTIFICATION HAS CHANGED`, independently verify the fingerprint.
Old keys are not removed automatically. Connecting has a 10-second timeout;
an established but unresponsive server normally disconnects in about 45 seconds.
Disconnect an interactive session with Enter, `~`, `.`.

QEMU Guest Agent needs `/dev/virtio-ports/org.qemu.guest_agent.0` in the guest.
If absent, the Proxmox operator first verifies machine identity and enables
**Options → QEMU Guest Agent**. The virtual device change may need a full
power-off and fresh start. Inside the server, check:

```sh
ls -l /dev/virtio-ports/
systemctl status qemu-guest-agent
```

Installing the service alone does not create the missing virtual port.
For direct SSH to a Proxmox VM, the guest agent is optional; `doctor` warns
if inactive while still checking hostname, architecture and system partitions.

<a id="reset"></a>

## Remove a managed VM

Set `allowReset = true;` in its local declaration, then:

```sh
nixberry reset vm-demo-libvirt
nixberry reset vm-demo-libvirt --apply
```

The first command only verifies the plan. The second **removes the VM and its
verified owned resources**. The declaration remains on disk.
Reset needs no system or media build and does not apply to offline VMs or RPi.

It verifies owner, actual identity, disks, sharing, snapshots and links.
A running VM is shut down gracefully with a deadline, then revalidated.
Uncertainty causes failure; there is no forced shutdown or blind remnant deletion.

For QEMU/KVM (libvirt), parent directories of the disk and NVRAM may be symlinks.
The preview lists the links and resolved file paths without prompting. With
`--apply`, an interactive terminal and an explicit `y` (or `yes`) are required
before shutdown; Enter, `n` or end of input cancels without changes. Piped input
does not satisfy this confirmation. Paths without directory symlinks retain
the usual noninteractive `--apply` behavior. There is no confirmation bypass.

Reset verifies the resolved paths and file identities again after confirmation
and graceful shutdown. A changed link target or replaced file stops further
changes. Links replacing the disk or NVRAM file itself, hard-linked files,
snapshots and shared resources remain rejected. Only the verified VM files are
removed; directory symlinks and their target directories remain. If a change
or failure occurs after the VM definition has been removed, inspect VM and
storage state before retrying; remaining files are not blindly cleaned up.
The local account also needs read access to the QCOW2 for `qemu-img` verification.
Permission errors stop before confirmation or shutdown; reset does not retry
with elevated privileges or change filesystem permissions.

| Platform | Resources and verification boundaries |
| --- | --- |
| QEMU/KVM (libvirt) | Owned QCOW2, NVRAM, domain references, backing images and snapshots; parent-directory symlinks require resolved-path verification and interactive confirmation. |
| Hyper-V | Owner, actual VM ID, disk and checkpoints; shared media in `media/` remains. |
| Proxmox VE | VMID/node, system and EFI volumes, own ISO, snapshots and VM/container references. |
| VirtualBox | VM, disk and dedicated directory; media registry and snapshots are checked. |
| VMware Workstation | Dedicated directory, VMX and standalone VMDK; running VMs and managed storage are checked. Stopped VMX files elsewhere are not scanned and GUI inventory is not changed. |

Directory-symlink confirmation is currently specific to local libvirt storage.
Hyper-V, Proxmox VE, VirtualBox and VMware Workstation retain their existing
link and storage checks. This applies to both image and ISO VM installations;
offline declarations and RPi have no reset operation.

After interruption, inspect `status` and storage before continuing.
Operations are not retried automatically. `cleanup` remains a preview.
Deleting a declaration file does not remove the VM or disk.
See [tests and safety checks](test-plan.md).
