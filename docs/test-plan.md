# Testing NixBerry

[← Main guide](../README.md) · [Platform contract](platform-consistency.md)

Separate static review, isolated regression tests, builds, live deployment and
running-system verification. A built image does not prove boot; a mock does
not prove a real hypervisor works.

## Isolated checks

The backup controller tests compare policy validation and operation availability
for all five VM backends and RPi, including image/ISO and offline/direct-access
variants. Provider fixtures check explicit recipe selection, snapshot/target
identity, JSON/error streams, no provider execution during dry-run, private timer
export and refusal to overwrite an existing export. Completion tests use only
local declarations/receipts. Configuration-specific tests must additionally cover
crypto EOF, interrupted transport, retained-copy verification, concurrent access
and retention refusal for partial, corrupt, unknown or changed state.

Live backup acceptance is separate: use an authorized isolated source/restore
target and host fence, capture through the main CLI, authenticate complete payloads,
restore only into an empty distinct target and activate recovered configuration
separately. Verify existing data without recreating it, numeric ownership and
service identities, then gracefully reboot and repeat application checks. Keep
protected evidence outside Git. Timer export/validation does not prove scheduled
execution or authorize activation; backup tests do not authorize VM creation,
reinstallation or removal. See [the backup contract](backups.md).

The named-adapter contract is exercised by `test_multinic.py` across all five VM
backends, including guest addressing, LUKS initrd selection, adapter identity and
management endpoint rejection. For live verification, use a separately authorized
disposable VM with isolated networks and an independent client. Check image and
ISO boot, adapter names/MACs, primary SSH selection, secondary addressing, rejection
of altered or extra adapters, and reset preview after hardware changes. Test both
supported host operating systems where available; isolated tests do not establish
live backend support or authorize changes to existing machines.

On x86_64 Linux, run the same isolated checks as CI from the recipe root:

```sh
nix develop --no-write-lock-file path:.#ci --command python3 management/deployment/check_ci.py
```

Nix must be installed and able to build local derivations. The `ci` development
shell supplies the remaining tools from the recipe lock without replacing the
installed Nix client. The first run downloads locked sources and runtime
dependencies. It builds management tools and small test artifacts, without
building guest systems, images or installation media.

The runner copies tracked working files into a temporary recipe with a test-only
public key. It excludes ignored and untracked files, including local hosts,
VM/RPi declarations and private linked configurations. Tracked absolute links,
links leaving the recipe and tracked paths through symbolic-link directories
are rejected. The original checkout and its local identity are preserved.
Add new source files to the Git index before checking them locally.

[GitHub Actions](../.github/workflows/checks.yml) runs on pushes, pull requests
and manual dispatches, with separate `regression` and `inventory` jobs. They
require no repository secrets, administrator keys, SSH agent or hypervisor.
Repository access is read-only and checkout credentials are not retained.
Both jobs run catalogue checks and reject stale generated translations before
compilation can rewrite them. Select either job locally with:

```sh
nix develop --no-write-lock-file path:.#ci --command python3 management/deployment/check_ci.py --suite regression
nix develop --no-write-lock-file path:.#ci --command python3 management/deployment/check_ci.py --suite inventory
```

The regression job checks Python, Nix, Bash and PowerShell syntax, unresolved
Python names, shell errors, workflow syntax and all five Python test suites.
Missing tools and skipped tests fail the job. The inventory job evaluates
isolated inventory variants across all five VM backends, including the
Linux and Windows example host declarations, and builds the empty CLI. RPi-specific
installation behavior is covered by mocked regression checks; CI does not
prove physical recovery, boot or live deployment on any platform.

For focused checks, use tools from the development shell and English defaults:

```sh
export NIXBERRY_LANG=en
python3 management/deployment/compile_locales.py --check
python3 management/deployment/compile_locales.py
python3 -m unittest discover -s management/deployment -p 'test_*.py'
python3 -m unittest discover -s management/libvirt -p 'test_*.py'
python3 -m unittest discover -s management/hyperv -p 'test_*.py'
python3 -m unittest discover -s management/rpi -p 'test_*.py'
python3 -m unittest discover -s management/proxmox/lab -p 'test_*.py'
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
| CI export excludes local data and unsafe links | `test_ci.py` |

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
nix build --no-link path:.#emcx
nix run path:.#emcx-tests
nix build --no-link path:.#hstr path:.#packages.x86_64-linux.hstr.tests.scroll
```

emcx needs `unshare -Ur` and runs TUI checks in tmux. HSTR uses temporary history.
Select the appropriate architecture for ARM packages.

## Live scenario matrix

### Encrypted Raspberry Pi root

Use a verified encrypted backup and prepared SD/USB recovery media. Live physical
disk installation and reboot require separate authorization and exact board/disk
identity checks. A build or local bootloader test does not prove RPi cold boot.

Check that plaintext defaults remain unchanged, private boot keys are absent
from the built initrd, and RP1, USB storage, cryptsetup and the declared static
network are available before root. Verify that missing runtime secrets abort a
generation update before the previous default generation is replaced.

Reject changed recovery SSH keys, mismatched public/private boot keys, a
different media policy and mounted or wrong-serial SSDs before formatting.
Install LUKS2 with runtime credentials, then verify the mapped root and original
physical SSD identity. Test a locked cold boot, wrong passphrase, changed boot
SSH key, successful unlock, installed-system verification, repeat cold boot and
an update that preserves runtime initrd-secret installation. Confirm persistent
application state survives the authorized migration.

### Encrypted VM images

Use a new isolated recipe, per-test SSH identities and a fresh disk. Never
reencrypt, reinstall or delete an existing VM as part of these checks.

For each available VM backend and image format:

1. Prepare a LUKS image outside the recipe and store. Confirm that neither
   the recipe, derivations, export nor logs contain the test passphrase.
2. Review `plan`, create the new VM and boot it. Confirm that initrd SSH is
   reachable while ordinary services are unavailable and the root stays locked.
3. Reject a wrong passphrase and a changed pinned SSH host key. Neither case
   may start the installed system or silently relax key verification.
4. Unlock through the pinned initrd identity. Verify LUKS2, the mapped root,
   the expected backing partition, boot partition, system and required services.
5. Shut down gracefully and boot the same VM again. It must wait for a new
   unlock; unlocking once must not install a persistent disk key.
6. Check export and offline import, including the public unlock metadata.
   Refuse corrupted media, changed identity/policy and encrypted ISO requests.

Confidential requests must fail before creation or key release on unsupported
implementations. An intentional refusal is evidence of that guard, not evidence
of confidential execution. Successful confidential execution additionally needs
real supported hardware, measured launch and independent attestation-bound key
delivery; mock evidence cannot substitute for these.

Unit tests and evaluated options cover declaration and validation contracts.
Image creation proves preparation only. A libvirt boot does not prove Hyper-V,
Proxmox VE, VirtualBox or VMware Workstation behavior.

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
