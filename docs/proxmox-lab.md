# Nested Proxmox on Nixon

[← Main guide](../README.md) · [VM platforms](vm.md) · [Testing](test-plan.md)

This dedicated lab tests the Proxmox adapter. It is a Proxmox VE host,
not another NixOS server profile. The helper is explicitly restricted to the
local Nixon host with nested AMD KVM.

- Libvirt VM `nixberry-pvelab`, 6 vCPUs, 12 GiB RAM, `host-passthrough` CPU, UEFI.
- `/home/shared/vms/nixberry-pvelab/system.qcow2` (32 GiB) and
  `data.qcow2` (64 GiB), with distinct serials.
- Dedicated libvirt NAT network `nixberry-pvelab`, bridge `virbrpve0`,
  subnet `192.168.78.0/24`. DHCP reserves `192.168.78.10` for its single NIC.
- Inside Proxmox: `vmbr0`, staging `/var/lib/nixberry` and `lab-vm` LVM thin
  storage on a separate virtual data disk.
- Neither VM nor network autostarts. Other VMs and the existing `default`
  network are not changed by preparation.

## Access and routine checks

Web: **https://192.168.78.10:8006**, account `root`, realm **Linux PAM**.
The password exists only in
`~/.local/state/nixberry/proxmox-lab/root-password` with mode `0600`.
Installation answers and SSH known hosts share that private directory.
The web UI uses a certificate from the Proxmox host's own CA.

```sh
ssh root@192.168.78.10
virsh -c qemu:///system domstate nixberry-pvelab
virsh -c qemu:///system net-start nixberry-pvelab # only if the network is inactive
virsh -c qemu:///system start nixberry-pvelab   # only if the VM is stopped
```

Verify SSH, web/API, VM identity, available KVM and nested QEMU startup.
A running libvirt process does not prove Proxmox health.
After installation, check that automatic installation media is detached in
persistent VM configuration as well.

Add `pvelab-proxmox` to `hosts.local.nix` using the Proxmox template:
`sshHost = "192.168.78.10"`, `hostName = "pvelab"`, `node = "pvelab"`,
`storage = "lab-vm"`, `isoStorage = "local"`, `bridge = "vmbr0"`.
Prepare a NixBerry guest:

```sh
nixberry new-proxmox-vm vm-demo-proxmox --host pvelab-proxmox
nixberry plan vm-demo-proxmox
# After reviewing the plan:
nixberry reproduce vm-demo-proxmox --apply
```

This creates a new VM inside Proxmox. Check free VMID, capacity, `lab-vm`
storage and `vmbr0` bridge in the declaration. Default method is `image`;
add `--method iso` for ISO installation.

```sh
nixberry status vm-demo-proxmox
nixberry ssh vm-demo-proxmox
nixberry doctor vm-demo-proxmox
nixberry verify vm-demo-proxmox
nixberry cleanup vm-demo-proxmox --json
```

`cleanup` protects the declared VM and reports capacity; physical thin-storage
allocation may remain unknown. Repeated `plan` rejects the existing target.
Read current DHCP addresses from `status`, not as permanent declaration values.

## Repeatable preparation of a new lab

From the NixBerry recipe root:

```sh
pvelab_tools=$(nix-build management/proxmox/lab/tools.nix --no-out-link)
export PATH="$pvelab_tools/bin:$PATH"
python3 management/proxmox/lab/create.py --dry-run
python3 management/proxmox/lab/create.py --apply
```

`--apply` downloads official Proxmox VE 9.2-1 ISO and verifies its pinned SHA-256.
The official `proxmox-auto-install-assistant` prepares automatic installation.
Only the new `vda` is installed; `vdb` remains blank.
Keyboard is `en-us`, country is Czechia and timezone is `Europe/Prague`.
Root gets a random password and the public key from `admin.pub`.
Private credentials do not enter Git or the Nix store.

After installation finishes and the host boots from disk:

```sh
python3 management/proxmox/lab/finish.py
python3 management/proxmox/lab/finish.py --apply
```

Finalization verifies UUID, hostname and both disk serials, then creates LVM thin
storage on the blank data disk. Recognized data is not overwritten.
The first SSH key is pinned in the lab's private `known_hosts`.
Also verify it for normal SSH before using ordinary NixBerry commands.

### Update the lab

Before load testing, inspect `uname -r`, KVM and available updates inside the lab.
Repositories must match its Proxmox version and subscription.

```sh
uname -r
apt list --upgradable
```

Update the host separately using its Proxmox administration procedure, with owned
test VMs gracefully stopped. After reboot, verify actual kernel, KVM, storage
and both deployment methods again.

`create.py` refuses existing VM, UUID, network, disks or private state.
It is not a reset tool and never removes partial results automatically.
After interruption, inspect exact state first; an existing disk is never permission
to overwrite it. If the old VM and disks were removed, retain metadata and private
state outside default locations. Remove a remaining network only after checking
UUID, subnet and absence of VM references. Official ISO may be reused after its
checksum passes. Verify a new VM through libvirt and private known hosts before
replacing a key; back up the old entry. Assistant `Error:` stderr is treated as
failure even when it returns zero.

### Capture a kernel panic

The VM serial port writes on Nixon to
`/var/log/libvirt/qemu/nixberry-pvelab-serial.log`.
Libvirt appends on subsequent starts. This host-side log survives unavailable
guest SSH or a Proxmox kernel panic:

```sh
sudo tail -F /var/log/libvirt/qemu/nixberry-pvelab-serial.log
```

`create.py` configures capture; `finish.py --apply` adds
`/etc/default/grub.d/99-nixberry-serial.cfg` inside Proxmox and runs `update-grub`.
Kernel output uses the screen and `ttyS0` at 115200 with `loglevel=7`;
GRUB also enables serial output. Parameters take effect on the next boot.
Verify `cat /proc/cmdline` in Proxmox and new kernel output in the host serial log.

A libvirt `running` state does not prove Proxmox health: QEMU can stay alive
after a guest panic. Preserve serial logs before reboot; the guest journal may
not contain the last error or stack trace.

Sources: [official ISO](https://enterprise.proxmox.com/iso/),
[Proxmox installation assistant](https://github.com/proxmox/pve-installer).
