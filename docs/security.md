# Encrypted VM disks

[← Main guide](../README.md) · [CLI](cli.md) · [Platforms](platform-consistency.md)

`security.mode` selects `none` (the unchanged default), `luks`, or
`confidential`. LUKS protects stored disk contents. An ordinary hypervisor can
access an unlocked VM's memory; LUKS does not protect against its administrator
during operation. It also does not protect against a compromised guest.

## Availability

The LUKS image preparation and remote-unlock interfaces cover QEMU/KVM
(libvirt), Hyper-V, Proxmox VE, VirtualBox and VMware Workstation. The same
guest disk layout applies to local and remote hosts, including Linux and
Windows hosts supported by the existing adapters. Preparation requires a
trusted Linux machine with Nix, KVM, QEMU, OpenSSH and a writable tmpfs.
Offline preparation does not require access to a target hypervisor.

Encrypted ISO installation is rejected before deployment. It must not silently
install an unencrypted disk. Existing unencrypted image and ISO behavior remains
available with `security.mode = "none";`.

`confidential` declarations express a stronger requirement and are currently
rejected before deployment or key release. Measured launch, attestation policy,
and attestation-bound key delivery are not implemented. A hardware capability
flag or ordinary remote SSH unlock is not sufficient to claim this protection.
There is no fallback to `luks` or `none`.

Raspberry Pi 5 uses separate SD recovery and physical disk declarations.
These VM options do not add encryption to RPi installations.

## Prepare and deploy

Create a declaration, then edit the network, resources and private output path:

```bash
nixberry new-libvirt-vm vm-demo-encrypted --host HOST --security luks
```

```nix
security = {
  mode = "luks";
  unlock = "manual";
  port = 2222;
  imageDirectory = "/absolute/private/path/vm-demo-encrypted";
};
```

The initrd SSH port must differ from the installed system's SSH port (22 by
default), so verification can distinguish a locked boot from ordinary services.

The output directory must not already exist. Keep it outside the recipe and
Nix store. It contains the encrypted system disk and `encryption.json`, which
binds its checksum, VM identity, system and boot SSH public key.

```bash
nixberry prepare-image vm-demo-encrypted --dry-run
nixberry prepare-image vm-demo-encrypted
nixberry plan vm-demo-encrypted
nixberry reproduce vm-demo-encrypted --apply
```

`plan` validates prepared media; run `prepare-image` first. Neither declaration
creation nor planning prepares an encrypted disk or reads a passphrase.

Preparation asks twice for a passphrase of at least 16 bytes. `--key-file FILE`
supplies a runtime passphrase instead; the file must be regular, private,
owned by the current user and not a symlink. Never put it in the recipe,
declaration, command arguments, environment, Git or Nix store. Keep a separate
secure backup: no recovery passphrase is generated automatically.

The trusted builder creates LUKS2 before writing the root filesystem. Temporary
password copies use tmpfs; the password is not installed in the image. A fresh
SSH key is generated for the initrd. Its private key is included in the
unencrypted boot environment and must never be reused as a normal server or
user key. The encrypted root contains the source needed for future bootloader
updates. The boot partition, partition layout and LUKS metadata remain visible.
Discard and unencrypted swap are not enabled by this mode.

An image has its own volume key. Do not clone one prepared image to provision
independent instances: prepare each instance separately. Updating a declaration
does not reencrypt an existing disk or change its keys.

## Unlock and verify

On cold boot the guest obtains its declared static address or a DHCP lease in
initrd. It waits for the passphrase before starting ordinary services.
SSH authorization uses the recipe's `admin.pub` and restricts early-boot access
to `systemctl default`, including repeated password requests. In an interactive
deployment, the installer attempts remote unlock using a discovered address or
the declared static address. Guest agents do not run before root is unlocked:
Hyper-V, Proxmox, VirtualBox and VMware may therefore have no early DHCP address
to report. Obtain it from the network's DHCP server or configure a static address.
For separate startup, or a deployment without an interactive terminal:

```bash
nixberry unlock vm-demo-encrypted --address 192.168.50.20
nixberry install vm-demo-encrypted --apply
nixberry verify vm-demo-encrypted
```

Unlock pins the initrd public key from local preparation and rejects changed or
unknown keys. It opens a fresh SSH connection with only the prepared Ed25519
host key, disabling connection sharing, dynamic host-key commands, DNS key
trust and automatic host-key updates. SSH forwarding is disabled. It uses the
declared SSH jump host
where the existing backend transport requires one. The boot environment's SSH
identity is separate from the installed server's identity. SSH disconnecting
during switch-root is not evidence of a successful boot: `install` and `verify`
must still check the installed system. Unlock does not reboot, create or format
a VM, and `--dry-run` does not connect or read a passphrase.

DHCP can assign different addresses to initrd and the installed system. Use
the current early-boot address for `unlock`; after unlocking, `status` and
installation verification discover the running system's address again.

A checksum failure or mismatched image binding requires investigation and a
new preparation if the declaration changed. Never bypass verification by
editing the checksum. Retain old prepared media for recovery until replacement
media have been verified; do not overwrite or redeploy over an existing VM.

Every cold boot requires unlocking. Suspend, memory snapshots and saved VM
state may retain keys and plaintext in memory: do not treat them as locked
disk backups. Disk encryption does not provide rollback protection, availability
or integrity of the unencrypted boot environment. A hostile host can replace
that environment and steal a later passphrase; pinned SSH alone does not
prevent that attack.

## Offline handoff

```bash
nixberry export vm-demo-encrypted --output ~/vm-demo-encrypted-bundle
```

Export checks the prepared image against the frozen recipe and copies encrypted
media without rebuilding them in the Nix store. The bundle includes public
unlock metadata and checksums, never the disk passphrase. Keep the passphrase
separate from both media and backups. After relocation, set
`security.imageDirectory` in the local declaration to the bundle directory
before using `unlock`; retain the original trusted public-key metadata.
