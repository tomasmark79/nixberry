# Roadmap

Items on this page are future work, not supported features or deployment
instructions. See [security](security.md) for the implemented LUKS workflow.

## Automatic remote LUKS unlocking

Status: deferred. Keep manual SSH unlocking as the current workflow.

Explore a central service that lets enrolled machines unlock their LUKS root
volumes during early boot. The service should be independently bootable and
reachable by remote clients. Tang and Clevis are candidates; the implementation
and declaration schema remain undecided.

### Security requirements

- A copied disk must not contain everything needed to obtain automatic unlock.
  A client private key stored in the unencrypted boot image can be cloned;
  TLS and client certificates alone do not solve that problem.
- Require an independent authorization condition, such as a hardware-protected
  identity with an appropriate boot-state policy, or explicit operator approval.
  Hardware identity protection is separate from memory encryption. Document
  the guarantees of each supported policy before enabling it.
- Preserve an independent manual passphrase and pinned SSH recovery path.
  Service failure or denial must leave the disk locked and recovery available.
- Keep secrets outside declarations, Git, the Nix store, manifests and logs.
- Support enrollment, disabling devices, credential rotation and audit records.
  Disabling future requests cannot relock a running system or revoke an unlock
  secret that an attacker has already obtained.
- Define service-key backup, recovery and rotation without a circular dependency
  on the same unlocking service.

### User experience and platform scope

- Declare each service once and reference it by name from target declarations.
  Keep trust configuration explicit and private credential provisioning separate.
- Use a consistent policy across QEMU/KVM (`libvirt`), Hyper-V (`hyperv`),
  Proxmox VE (`proxmox`), VirtualBox (`virtualbox`), VMware Workstation
  (`vmware`) and Raspberry Pi 5 (`rpi`). Unsupported policies must fail clearly.
- Cover VM image and ISO installation, offline handoff, and RPi recovery media;
  preparing artifacts must not silently enroll or authorize a device.
- Provide understandable plans, status and recovery messages. Keep manual
  unlocking as the default until the security model and live boot behavior
  have been validated.
