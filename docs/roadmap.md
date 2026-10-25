# Roadmap

Items on this page are future work, not supported features or deployment
instructions. See [security](security.md) for the implemented LUKS workflow.

## Portable server configurations and verified recovery

Status: primary milestone. Develop and validate the replacement server in a VM
first, then verify it on Raspberry Pi 5. Recovery of the complete server role
and its application data across these platforms remains to be demonstrated.

The intended workflow is to declare a server's services, deploy it, back up its
data and secrets, and recover the same role on different hardware after failure.
Keep service configuration independent of platform boot support, device
identities, network addresses and storage layout. Build the system separately
for each architecture; do not assume an ARM disk image can boot on x86_64.

### First recovery milestone

- Inventory the existing server's services, dependencies, data, secrets and
  storage requirements without changing the production system. Keep concrete
  infrastructure details and credentials outside the public recipe.
- Reproduce the service configuration in an isolated VM with separate network
  identity. Prevent test services from affecting production users, scheduled
  jobs, mail delivery or external integrations.
- Define persistent application data separately from reproducible system
  configuration. Large datasets and secrets must not become Nix-store inputs
  or be embedded in installation images.
- Establish encrypted, incremental and versioned backups on storage independent
  of the target. Include application-consistent database backups, required
  secrets, retention, integrity checks and a documented restoration procedure.
- Restore the same role into a second VM from the declaration and backup,
  without depending on the original machine being available. Verify application
  behavior, data completeness and permissions; record recovery time and the
  recoverable backup point outside user documentation.
- Check ARM package availability and build the same service configuration for
  Raspberry Pi 5. Verify real boot, storage, network and representative workload
  performance there; VM success alone does not establish RPi compatibility.

Use the existing VM backends for this workflow and state which combinations
have actually been verified. Production cutover is a separate, explicitly
authorized operation. Home Manager is optional user-environment support; disk
encryption remains an independent target policy. Encryption conversion and
automatic unlocking are not prerequisites for this milestone.

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
