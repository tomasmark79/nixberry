# Testing NixBerry

[← Main guide](../README.md) · [Platform contract](platform-consistency.md)

Separate static review, isolated regression tests, builds, live deployment and
running-system verification. A built image does not prove boot; a mock does
not prove a real hypervisor works.

## Isolated checks

From the recipe root, with Nix, Python, Bash and GNU gettext available:

```sh
python3 management/deployment/compile_locales.py
python3 management/deployment/compile_locales.py --check
python3 -m unittest discover -s management/deployment -p 'test_*.py'
python3 -m unittest discover -s management/libvirt -p 'test_*.py'
python3 -m unittest discover -s management/hyperv -p 'test_*.py'
python3 -m unittest discover -s management/rpi -p 'test_*.py'
python3 management/deployment/check_inventory.py
```

Use English defaults for the regression suite; individual translation tests
select Czech explicitly. Tests use temporary declarations and mocked transports.
Some invoke Nix with small fixture artifacts or test real Python-to-Python
transport. They do not require reinstalling a live server. Recipe fixtures use
test keys rather than the administrator's local identity.

| Area | Checks in `management/deployment/` |
| --- | --- |
| CLI capabilities and rejected options | `test_cli.py`, `test_platform_contract.py` |
| Configurations and all backend declarations | `test_configurations.py`, `test_scaffold.py`, `test_methods.py` |
| Tool packages, image/ISO manifests, reset without media | `test_platform_contract.py` |
| Portable adapter, identities, disks, transfer | `test_portable.py` |
| Hyper-V media selection and transport | `test_hyperv_media.py`, `management/hyperv/test_*.py` |
| Reset and rejected unsafe steps | `test_reset.py` and backend checks |
| libvirt directory-symlink confirmation and revalidation | `management/libvirt/test_reset_symlinks.py`: temporary files and mocked hypervisor operations; no live VM removal |
| Completion, aliases, online/offline and direct SSH | `test_completion.py` |
| Output, diagnostics, colors, languages and catalogues | `test_diagnostics.py`, `test_messages.py`, `test_message_contract.py` |
| Metadata comparisons without importing unsigned paths | `test_diffing.py`, `test_closure_metadata.py` (real local `nvd` when available) |
| Campaign isolation and resource tracking | `test_campaign.py` |

Nixon's separate shell integration check is run from its own repository:
`nix build --no-link path:.#checks.x86_64-linux.nixberry-shell`.
It substitutes Nix, editors and deployment, including generated launchers/loaders.

## Prebuilt media checks

With `qemu-img` and mtools, inspect QCOW2, VDI and VMDK:

```sh
python3 management/deployment/check_images.py /path/system.qcow2 /path/system.vdi /path/system.vmdk
```

Checks read format, capacity, GPT, ext4, UEFI fallback and boot entries referencing
kernel, initrd and system. Images are neither mounted nor changed.
This does not replace a real boot test.

Application checks:

```sh
nix build --no-link path:.#mc
nix run path:.#mc-tests
nix build --no-link path:.#hstr path:.#packages.x86_64-linux.hstr.tests.scroll
```

MC needs `unshare -Ur` and runs TUI checks in tmux. HSTR uses temporary history.
Select the appropriate architecture for ARM packages.

## Live scenario matrix

Test both methods for each supported backend/host combination.
Windows uses SSH; local libvirt, VirtualBox and VMware run on Linux.

| Group | Scope |
| --- | --- |
| ON | Declaration, plan, deployment, disk boot, SSH, doctor/verify, update, generations and cleanup. |
| OFF | Every backend's image/ISO and full RPi SD export, manifests, recipe, instructions and checksums. |
| HAND | Real manual disk import or exported ISO installation, then SSH management. |
| NET | DHCP and static IP, gateway, DNS and reachability in a dedicated network. |
| NATIVE | Existing servers and RPi, read-only checks only. |

Alternate ON entry points: `reproduce`, `provision` and separate `create/install`.
Check method override with `--method` and configuration selection.

Each case must demonstrate:

1. Target absence and dedicated owned resources before creation.
2. Correct format, capacity, UEFI, controller, network and agent.
3. Prebuilt disk without formatting for image; blank disk and installer for ISO.
4. Boot of the exact system path from disk without an attached ISO.
5. SSH, identity, network, services, `doctor` and `verify`.
6. `diff` before update, a read-only plan, activation, then `diffg`.
7. Rejection of repeated creation of an existing VM.
8. Cleanup preview and safe removal of only owned test resources.

Separately test RPi SD recovery, SSD identity, boot without the card and normal
updates. Live writes need a specific target and explicitly authorized scope.
Availability alone is not permission to update or recover a machine.

## Error and safety cases

- Name, UUID, MAC, VMID and foreign-owner collisions must stop writes.
- Incorrect host, disk, checksum, profile or capacity must prevent installation.
- Reset checks snapshots, backing images, sharing, links and identity changes
  between plan and removal. No permission or no `--apply` means no deletion.
- Failed graceful shutdown must not escalate to forced power-off.
  Failed ISO installation must not detach media early or reboot into an unverified system.
- Unreachable SSH, missing tools and missing agents must fail within bounded
  time with actionable diagnostics.
- Interrupted transfer or lost responses must not blindly repeat creation.
- Check English and Czech output, fallback, stdout/stderr, JSON, exit codes,
  TTY, `NO_COLOR` and `TERM=dumb`.

## Campaign runner

[management/deployment/campaign.py](../management/deployment/campaign.py)
prints its matrix without `--apply`. Live runs require prepared hosts,
dedicated resources and explicit settings:

```sh
python3 management/deployment/campaign.py
python3 management/deployment/campaign.py --apply \
  --run-root /home/shared/tmp/nixberry-campaign-ID \
  --settings /home/shared/tmp/nixberry-campaign-settings.json
```

`--case ID` selects a case; `--resume` continues an existing report.
Settings may include `hostOverrides` and dedicated `networks`.
See the [nested lab guide](proxmox-lab.md) for that host.

The runner explicitly pins a private recipe copy so shell settings cannot
redirect it. Results and logs live outside the source repository.
It uses `vm-demo-test-*` names, unique UUIDs and resource evidence.

Online cleanup uses public `reset`: enable removal in the private declaration,
check the plan, then pass `--apply`.
HAND import is verified against manual-creation evidence. VMware uses internal
cleanup because its manual imports have no automatic-management marker.
Uncertain remnants are retained for review.

## Evidence

Record recipe revision, declaration/configuration, host, VM identity, method,
expected system, commands, exit codes and both streams. Never copy private keys,
passwords or tokens. Record build, activation, boot and live checks separately.

Keep evidence in the run directory, not user documentation.
Runner states are `NOT_RUN`, `RUNNING`, `PASS`, `FAIL` and `BLOCKED`.
Explain specific failures or blocks and never mark untested scenarios successful.
If identity is uncertain or writes are partial, stop further writes and inspect
state. Resuming does not automatically repeat passing cases.
