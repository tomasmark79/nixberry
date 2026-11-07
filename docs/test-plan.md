# Testing NixBerry

[Main guide](../README.md) · [Platform contract](platform-consistency.md)

Match verification to the change. Static review and isolated regression checks do
not prove deployment; a built image does not prove boot. Keep logs and results
outside user documentation. Documentation-only changes need source/link checks,
not a full backend campaign.

## Isolated checks

On x86_64 Linux, run the same checks as CI from the recipe root:

```sh
nix develop --no-write-lock-file path:.#ci --command python3 management/deployment/check_ci.py
```

The locked `ci` shell supplies tools while retaining the installed Nix client.
The runner checks catalogue freshness before compilation, Python/Nix/Bash/
PowerShell/workflow syntax, unresolved names, shell errors, all Python suites and
isolated inventory variants. Missing tools, failed tests and skipped tests fail
the run. It builds management tools and small fixtures, not guest systems/media.
Select `--suite regression` or `--suite inventory` to narrow the run.

The runner copies tracked working files into a temporary recipe with a test key.
It excludes ignored/untracked hosts, machine declarations and private linked
configurations; add new public source files to the index before checking them.
Unsafe tracked links are rejected. The original recipe and local identities are
preserved, and no hypervisor or administrator credentials are needed.
[GitHub Actions](../.github/workflows/checks.yml) runs regression, inventory and a
separate privileged restore-publication job with read-only repository access.

For focused checks, use the development shell:

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

Tests use temporary declarations, test keys, mocked transports and selected small
real artifacts. English is the default; individual translation tests select Czech.
Find focused cases under `management/deployment/test_*.py` and backend directories:

| Change area | Required focus |
| --- | --- |
| CLI, declarations, methods, configurations | Capabilities, validation, scaffolding, help, aliases, completion and exported manifests across all applicable platforms. |
| Networking | Adapter order/MACs, primary management endpoint, secondary addressing, LUKS initrd selection and altered/extra adapter refusal. |
| Identity, storage, reset | Owner/UUID/disk/reference checks, links, graceful shutdown, revalidation and refusal of uncertain state. Libvirt symlink cases use temporary files/mocked operations. |
| Updates and comparisons | No-contact plans, generation/policy guards, capture leases, partial updates, archive staging and metadata comparisons without importing running-system code. |
| Capture and Restic | Ext4 cold/Btrfs modes, paths/writers, nested roots, timeout/restart failure, durable recovery, strict storage trust, receipts and full authentication. |
| Retention and health | Machine isolation, duplicate receipts, bad/future metadata, retained-payload authentication, changed inventory, exact deletion IDs and freshness boundaries. |
| Restore | Prepared guards, empty destinations, accounts/architecture, private credentials, interrupted publication/retry, unknown files, completion and activation exclusion. |
| Provider automation | Explicit recipe, source-loss reads, private timer exports, no execution on dry-run, run history, PID reuse and notification failures/deduplication. |
| Messages and output | English/Czech/fallback, placeholders, remote packaging, stdout/stderr, JSON, exit codes, TTY, `NO_COLOR` and `TERM=dumb`. |

## Disposable integration checks

The following [flake checks](../flake.nix) use isolated fixtures. For example:

```sh
nix build --no-link --no-write-lock-file path:.#checks.x86_64-linux.rpi-local-ssd
```

VM checks need KVM and temporary storage, without host root access. Run only
checks relevant to the change:

| `checks.x86_64-linux.NAME` | What to verify |
| --- | --- |
| `rpi-local-ssd` | Real partitioning of a disposable disk, ARM binfmt, closure/boot-file installation, mount protection and explicit repeated erase. |
| `snapshots` | Actual Btrfs capture, stable read-only view, writer recovery, nested-root refusal, mounted-reference protection and both update-generation locks. |
| `restic-backup` | Btrfs/ext4 sources with SFTP storage, explicit init/staging, source-timer execution, strict-host-key refusal, complete payload authentication, maintenance and source-loss reads. |
| `restore-preparation` | Writer/socket/timer and backup-job holds across boot/reboot, policy/machine-bound completion and preservation of other unit conditions. |
| `restore-publication` | Real filesystem ownership, timestamps/xattrs, overlay whiteouts, device refusal, tmpfs and bind-mount publication. |
| `restic-restore` | UEFI source, encrypted backup, source shutdown/declaration loss, read-only SFTP recovery, interrupted restore/reboot/retry and HTTP/SQLite application checks; unchanged destination identity. |
| `router` | Declared router behavior with isolated upstream/downstream clients. |
| `retrogaming` | Player policy, launcher paths, writable settings and Pegasus collection discovery in a virtual display. |
| `dosbox-pure` | Execution of a small original DOS program with dynamic recompilation. Also available for `aarch64-linux`. |

These prove only their exercised fixtures. Direct SSD preparation does not test
RPi firmware; x86_64 source/restore guests do not prove physical ARM boot or every
hypervisor transport. Run the ARM DOSBox check natively on a 16 KiB RPi kernel
to exercise that page-size boundary; emulation on a 4 KiB host does not prove it.

For backup integration, inspect authenticated account/architecture evidence and
numeric ownership, ACLs, hardlinks, sparse files, symlinks and xattrs. Nested child
captures must replace hidden parent data, including literal special characters
in paths. Verify retained payloads after pruning. Independent reads use Restic
`--no-lock`: prevent concurrent storage pruning during verification/restore.
A freshness check is neither full payload authentication nor a restore test.

## Prebuilt media checks

With `qemu-img` and mtools:

```sh
python3 management/deployment/check_images.py /path/system.qcow2 /path/system.vdi /path/system.vmdk
```

This reads format, capacity, GPT, ext4, UEFI fallback and kernel/initrd/system
boot entries without mounting or modifying images. It does not replace boot.
For shared shell applications, use `nix run path:.#emcx-tests` and the HSTR
package tests; emcx TUI checks need `unshare -Ur` and tmux.

## Live scenario matrix

Live creation, installation, shutdown, removal, updates and physical disk writes
need explicit authorization for dedicated targets/resources. A running machine
is not permission to change it. Use an isolated recipe and separate identities,
networks, storage and clients; production services must not receive test traffic
or send mail/jobs to real users.

| Variant | Acceptance |
| --- | --- |
| All five VM backends | Both image and ISO; supported Linux/Windows hosts; correct UEFI/controller/agent, capacity, disk boot and exact system path. |
| Online VM | `plan`, authorized deployment, SSH/doctor/verify, diff/update/generations, cleanup preview and scoped reset of owned test resources. |
| Offline VM | Disk/ISO export, manifest/recipe/checksums, actual manual import or installation, then direct SSH management. |
| VM networking | DHCP/static primary, gateway/DNS, named secondary adapters and refusal of altered identities/attachments. |
| Physical RPi | Correct board/SSD, direct preparation or SD recovery, boot without SD, network, updates and application persistence. |
| Existing servers | Read-only checks unless a separate write scope is authorized. |
| Backups | CLI capture, complete authentication after source loss, restore into an empty distinct destination, ownership and application checks, then an authorized reboot and repeat checks. |

For VMs, exercise `reproduce`, `provision` and separate `create/install`, method
and configuration selection. Verify target absence first. Image boots without
formatting; ISO installs only onto a blank disk, detaches after successful
installation/graceful shutdown and boots the installed system. Reject repeated
creation, foreign ownership, shared disks and stale identity. Inventory/cleanup
must not delete anything. Never force shutdown after a graceful-shutdown failure.

### Encrypted Raspberry Pi root

Follow [RPi encryption](security.md#raspberry-pi-5) with a verified backup and
prepared recovery media. Check exact board/disk identity, pinned recovery SSH,
matching runtime boot key and passphrase, mounted-disk refusal, LUKS2/root/boot
links, correct SSD serial and absence of secrets from store artifacts/logs.

Test a locked cold boot, wrong passphrase, changed boot SSH key, successful
unlock, ordinary verification, another cold boot and an update retaining runtime
initrd-secret handling. Missing secrets must stop a generation update before
replacing its previous default. Check USB Ethernet/storage and RP1 in initrd on
the actual hardware, plus persistent application data after recovery.

### Encrypted VM images

Use fresh declarations/disks and per-test keys. Prepare outside the recipe/store;
check that passphrases never enter artifacts or logs. For each backend, verify
locked boot and pinned initrd SSH before services, wrong-password/key refusal,
LUKS2 and backing-partition identity, unlock and installed-system verification.
A later cold boot must require unlocking again. Check encrypted offline handoff
and refusal of changed media/policy and encrypted ISO requests.

`confidential` requests must fail before creation or key delivery. That refusal
proves a guard, not confidential execution. A successful libvirt unlock does not
prove the other hypervisors; neither memory snapshots nor saved VM state count
as locked-disk backups.

## Campaign runner

[management/deployment/campaign.py](../management/deployment/campaign.py) prints
its matrix without `--apply`. Authorized live runs need prepared hosts, explicit
settings and dedicated resources:

```sh
python3 management/deployment/campaign.py
python3 management/deployment/campaign.py --apply \
  --run-root /home/shared/tmp/nixberry-campaign-ID \
  --settings /home/shared/tmp/nixberry-campaign-settings.json
```

`--case ID` selects a case; `--resume` continues a report. Settings may include
`hostOverrides` and dedicated `networks`. The [nested Proxmox lab](proxmox-lab.md)
is optional. The runner pins a private recipe and uses unique `vm-demo-test-*`
identities; logs/results stay outside Git.

Online cleanup uses public `reset` with private declaration permission and a
reviewed plan. Manual-import cleanup requires its recorded identity evidence;
VMware uses the runner's internal cleanup because manual imports have no automatic
owner marker. Uncertain remnants remain for review.

## Evidence

Record recipe revision, configuration/declaration, host and target identity,
method, expected system, commands, exit codes and both streams without secrets.
Record build, activation, boot and running application checks separately.
Runner states are `NOT_RUN`, `RUNNING`, `PASS`, `FAIL` and `BLOCKED`; explain
failures/blocks and leave untested variants unverified. Stop further writes when
identity is uncertain or writes are partial. Resume does not repeat passing cases.
