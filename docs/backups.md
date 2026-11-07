# Encrypted backups and recovery

[Main guide](../README.md#backups) · [CLI](cli.md) · [Testing](test-plan.md)

Use shared Restic tools unless the selected configuration supplies a separate
[controller provider](#controller-providers). Restic stores encrypted application
data and its recovery recipe on independent storage. It restores files into a
prepared replacement; it does not clone a disk or reinstall a machine.

The configuration owns application paths, writers, storage and schedules.
The target declaration owns hardware identity and `rootFilesystem`:

| Filesystem / capture method | Writer downtime |
| --- | --- |
| Btrfs, `snapshot` (default) | Stop writers, capture read-only subvolumes, resume writers, then upload. |
| Ext4, `cold` (default) | Keep writers stopped while reading and uploading their paths. |
| Btrfs, explicit `cold` | Same cold-capture behavior; no local snapshots. |

Changing `rootFilesystem` requires a separate installation, not an ordinary
update. Selecting Btrfs alone enables no backup jobs. Local snapshots remain on
the source disk and cannot protect against its loss.

## Configuration-owned Restic uploads

In the selected configuration, import the common base and declare real paths
and all units that can write or reactivate those writers:

```nix
{ nixberryModules, ... }:
{
  imports = [ nixberryModules.common ];
  nixberry.backup = {
    enable = true;
    applications.web = {
      paths = [ "/var/lib/web" ];
      stopUnits = [ "web.service" "web.socket" ];
    };
    restic = {
      enable = true;
      repository = "sftp:backupagent@nas.example:/NetBackup/rpi-demo";
      passwordFile = "/var/lib/nixberry-backup/password";
      repositoryIdFile = "/var/lib/nixberry-backup/repository-id";
      recoveryFile = "/var/lib/nixberry-backup/recovery.tar";
      ssh = {
        port = 7922;
        identityFile = "/var/lib/nixberry-backup/ssh-key";
        knownHostsFile = "/var/lib/nixberry-backup/known_hosts";
      };
      schedule = "*-*-* 03:30:00";
    };
  };
}
```

Replace the example service and paths with the configuration's actual data.
The catalogue does not discover unlisted applications or writers. Include
activating sockets and timers in `stopUnits`; an empty list explicitly permits
live reads. Declare symbolic-link targets as explicit data paths. Keep capture
state and snapshot storage outside application data.

Before relying on backups:

1. Deploy the configuration through the normal reviewed update procedure.
2. Provision the storage account, a dedicated SSH key, verified storage host key
   and repository password as runtime files on the source. For port 7922, the
   known-host entry is `[nas.example]:7922`.
3. For a new repository, explicitly initialize it, then stage the recovery recipe:

   ```sh
   nixberry ssh rpi-demo -- nixberry-restic backup-init --json
   nixberry ssh rpi-demo -- nixberry-restic backup-init --apply --json
   nixberry backup-stage rpi-demo --json
   nixberry backup-stage rpi-demo --apply --json
   ```

4. Create and fully verify a backup, preserve
   [independent recovery access](#independent-restic-storage-access), and test
   restoration into a distinct prepared machine.

Credential/archive files must be regular, root-owned files outside the Nix
store, with trusted parent directories and no untrusted write access. Passwords,
SSH private keys and `recoveryFile` need private permissions such as `0600`.
Host-key and repository pin files may be readable, but not writable, by others.
Never embed secret contents in Nix, Git, command arguments or exported media.

Initialization requires a new repository pin file. It refuses an existing pin
and does not adopt an existing repository. Ordinary backup never initializes
storage. To reuse an existing repository, restore its original trusted full
repository ID into `repositoryIdFile` along with its password, authorized SSH key
and verified host-key file; skip `backup-init` and stage the recipe. Preserve the
pin independently of storage rather than trusting a newly observed repository ID.
Both strict SSH trust/authentication and the pinned repository ID are
checked before writer shutdown; there is no password prompt, agent fallback or
automatic host-key acceptance. Absolute local repositories are supported for
isolated testing; other Restic storage backends are not accepted here.

For an explicitly accepted storage endpoint without post-quantum key exchange,
`nixberry.backup.restic.ssh.warnNoPqKex = false;` suppresses that advisory for
this repository only. It uses `WarnWeakCrypto=no-pq-kex`, retaining encryption,
algorithms, host-key verification and other diagnostics. Older SSH clients ignore
the unsupported option. Prefer upgrading the storage server.

### Recovery recipe staging

`backup-stage` prepares a selected, sealed recipe archive. Its plan lists files,
size and SHA256 without contacting the source. It includes the selected machine,
configuration, public administrator key and selected host, excluding unrelated
local declarations/configurations and runtime secrets. Custom code must live
under the standard project source directories or the selected configuration;
unsafe links and private key/secret source files are refused. The archive is
limited to 128 MiB.

Preparation must reproduce the exact expected system and policies in pure mode,
without changing locks or importing derivation results. Absolute machine-local
flake inputs are refused. Rebuilding still needs access to pinned dependencies;
the archive is not a Nix store backup.

With `--apply`, staging verifies the source identity, installed generation and
policy, then atomically binds the verified archive under the capture lock.
No writers are stopped and no repository is initialized. Missing/stale bindings
prevent backup before writer shutdown. Retry staging after a failed transfer.

`update --apply` prepares recovery code before transfer and activation, then
stages it after verified activation. A staging failure is a **partial update**:
the system may already be active, but backup refuses stale recovery code. Follow
the reported `backup-stage --apply` retry. Updates through other tools and
rollbacks require explicit staging. Activation refuses an active capture or
pending writer recovery, including across changed capture state directories.

### Create, inspect and verify

From a Linux controller with verified root SSH access:

```sh
nixberry backup rpi-demo --json
nixberry backup rpi-demo --apply --json
nixberry ssh rpi-demo -- nixberry-restic backups --json
nixberry ssh rpi-demo -- nixberry-restic verify-backup --snapshot SNAPSHOT_ID --json
```

Capture plans evaluate/freeze the recipe without contacting the source.
Execution checks identity, the exact running generation and installed policies
before invoking the guest tool. Source-side equivalents are
`nixberry-restic backup [--apply] --json`, `backups --json` and
`verify-backup --snapshot ID --json`, run as root.

A completed backup contains an encrypted payload and completion receipt. The
receipt is published only after upload, recovery bindings and writer recovery
succeed. Incomplete payloads remain unlisted and are not retention candidates.
Successful snapshot-mode upload removes its temporary local snapshot; failure
can retain it for inspection.

| ID / state | Meaning |
| --- | --- |
| 64-character Restic `snapshotId` | Full completion receipt ID; use for verification and recovery. |
| 32-character local capture ID | Source Btrfs snapshot; not a Restic restore point. |
| `recorded-captured` | Producer completed and small recovery bindings were read back. |
| `recorded-verified` | Full payload authentication and recipe hash verification succeeded. |

`verify-backup` streams the whole payload to discard without creating a restored
application tree. Freshness and inventory are not substitutes for that check,
and full authentication is not an application restore test. Restic uses a
decryption-capable key on the source; use separate repositories/storage access
where sources must not share decryption authority.

Capture uses bounded private tmpfs, disables Restic cache, and creates no local
application-data staging copy. Source services limit runtime storage to 512 MiB,
memory to 1 GiB and disable disk swap. `restic.timeoutSeconds` defaults to 3600
for the operation; writer recovery has its own timeout. Abrupt failures retain
a durable recovery record. Recover writers before another capture; never delete
the record to bypass that guard.

## Configuration-owned local snapshots

Btrfs configurations can use local capture independently of Restic:

```sh
nixberry snapshot rpi-demo --json
nixberry snapshot rpi-demo --apply --json
nixberry snapshots rpi-demo --json
nixberry snapshot-remove rpi-demo --snapshot LOCAL_CAPTURE_ID --apply --json
nixberry snapshot-resume rpi-demo --apply --json
```

Creation, removal and recovery only plan without `--apply`. Inventory reads the
verified source; `--dry-run` skips source access. Guest-side equivalents are
`nixberry-capture snapshot`, `snapshots`, `snapshot-remove --snapshot ID` and
`resume`, with `--apply` for writes. Local snapshots require Btrfs even when
Restic uses cold capture.

The default root is `/`, with storage at `/.snapshots/nixberry`. The standard
layout separates root, snapshot and swap subvolumes. Declare every nested data
subvolume, including container-created ones, in `nixberry.backup.roots` with
`path` and `snapshotDirectory`. Each snapshot directory must be on the same
filesystem outside its source subvolume. Mounts inside application data need an
explicit captured root; directory symlinks are rejected.

Capture checks filesystem identity, free space, mount coverage and nested
subvolumes before stopping writers. It captures while writers remain stopped
and resumes only the previously active units. Writer reactivation, timeout or
snapshot failure prevents successful publication. Failed restart blocks new
captures; `snapshot-resume --apply` retries recorded writers, as does the boot
recovery service.

A captured child root replaces its hidden subtree in ancestor snapshots during
Restic upload, including stale files under bind mounts. The tool does not upload
that hidden parent data. Retained snapshots consume source space as it changes;
`minFreeBytes` is a preflight threshold, not a reservation. Removal verifies
recorded paths, machine/filesystem/snapshot identities, parent UUID, read-only
state and mounted references. Failed or uncertain remnants are retained.

## Restic maintenance

Configure optional source timers alongside the backup schedule:

```nix
nixberry.backup.restic = {
  keepLast = 7;
  maxAgeSeconds = 129600;
  pruneSchedule = "*-*-* 05:00:00";
  checkSchedule = "hourly";
};
```

All four settings default to `null`. `keepLast` accepts 1–100000;
`maxAgeSeconds` accepts 1–31536000. Schedules require their matching threshold.
`schedule = null` leaves backup manual. Timers default to `persistent = false`,
so missed runs do not catch up. Use one capture scheduler for the same data.
These source jobs run without the controller and leave application writers
running during maintenance. Prepared restore targets hold them until completion.

### Restic retention

```sh
nixberry backup-prune rpi-demo --dry-run --json
nixberry backup-prune rpi-demo --json
nixberry backup-prune rpi-demo --apply --json
```

`--dry-run` reads policy only; the default verifies the source generation and
reads an actual repository plan. Applying revalidates under the capture lock,
refuses pending writer recovery, authenticates metadata and fully verifies the
newest retained payload/recipe before deleting explicit old receipt/payload IDs.
Duplicate receipts for a payload count once. Other machine identities,
incomplete captures and unknown snapshots remain untouched. The tool never
unlocks the repository. Guest equivalent: `nixberry-restic backup-prune`.

### Restic freshness checks

```sh
nixberry backup-check rpi-demo --json
nixberry backup-check rpi-demo --backup-access /private/access.json --max-age-seconds 129600 --json
```

Source checks use configured `maxAgeSeconds` and current machine identity.
Independent checks need an explicit age threshold and use the preserved
`sourceId`, including across replacements; neither source nor declaration is
needed. `--dry-run` skips storage access. Restic checks reject `--apply` and send
no desktop notifications. The source check service fails on an unhealthy result;
inspect its systemd status/journal.

Results are `ok`, `stale`, `missing` or `future`: exit `0` means healthy, `1`
means a finding or error. Checks authenticate receipt/manifest metadata and the
selected sealed recipe, not the full payload. Invalid metadata fails instead of
falling back to an older backup. Source command
`nixberry-restic backup-check --statistics --json` additionally reports distinct
completed restore points and the latest logical restored size for the current
machine. That size includes recipe/manifest, not repository disk usage.

## Independent Restic storage access

Preserve this private context, password, pins and authorized storage SSH
credentials on a recovery workstation, outside every recipe and the Nix store:

```json
{
  "version": 1,
  "target": "rpi-demo",
  "sourceId": "rpi:EXAMPLE_SERIAL",
  "repository": "sftp:recovery@nas.example:/NetBackup/rpi-demo",
  "repositoryId": "1111111111111111111111111111111111111111111111111111111111111111",
  "passwordFile": "/home/operator/.local/share/nixberry-recovery/password",
  "ssh": {
    "port": 7922,
    "identityFile": "/home/operator/.local/share/nixberry-recovery/ssh-key",
    "knownHostsFile": "/home/operator/.local/share/nixberry-recovery/known_hosts"
  },
  "runtimeDirectory": "/run/user/1000/nixberry-recovery",
  "timeoutSeconds": 3600
}
```

Copy the actual `sourceId` from `/etc/nixberry/backup.json` and complete
repository ID from `repositoryIdFile` **before source loss**. The target must
match the archive's original declaration. Substitute your own paths and UID;
the runtime directory must be tmpfs. For local storage, use an absolute repository
path and `"ssh": null`. Optional SSH `"warnNoPqKex": false` has the same limited
storage advisory effect described above.

Use mode `0600`, the recovery user's ownership, trusted parent directories and
no symlinks. Root is not required. A separate key may have read-only SFTP access
to the same pinned repository; no agent or automatic trust fallback is used.
Reads use Restic `--no-lock`, so **coordinate all independent verification and
restoration with storage pruning**. The source capture lock supplies no lease
for another reader.

```sh
nixberry backups rpi-demo --backup-access /private/access.json --json
nixberry verify-backup rpi-demo --backup-access /private/access.json --snapshot RECEIPT_ID --json
nixberry recover-source rpi-demo --backup-access /private/access.json --snapshot RECEIPT_ID --output /home/operator/recovered-source
nixberry recover-source rpi-demo --backup-access /private/access.json --snapshot RECEIPT_ID --output /home/operator/recovered-source --apply
```

Use a full receipt ID from inventory. These reads need Linux, but neither the
original source nor its declaration. Any NixBerry checkout can supply the tools.
`--backup-access` explicitly selects Restic even when a provider is declared;
`--dry-run` skips storage access.

Source recovery plans by default. Applying authenticates the receipt, manifest,
archive and source/generation/policy binding, then writes `recovery.json` and
`recipe/` in a new private directory. It never evaluates or activates recovered
code. An interrupted extraction leaves `INCOMPLETE`; inspect it and use a new
output for retry. Recovering code does not restore application data.

## Prepared restore destinations

Install or update a **distinct** destination with its own hostname, network and
hardware/VM identity, the required service configuration, and:

```nix
nixberry.backup = {
  enable = true;
  restore.prepare = true;
  applications.web = {
    paths = [ "/var/lib/web" ];
    stopUnits = [ "web.service" "web.socket" ];
  };
};
```

Keep these application definitions in configuration-owned shared modules;
do not import another named configuration. `restoreUnits` defaults to nonempty
`stopUnits`. Applications captured without shutdown must declare `restoreUnits`
explicitly; `[]` means no managed destination writers. Include activators.
Management/networking units cannot be held. Source backup/prune/check services
and timers are held until restoration completes as well.

The management network and storage must work independently of held application
services. Destination application paths must be empty/absent and writers
inactive. Inspect without changing services or files:

```sh
nixberry ssh rpi-demo-restore -- nixberry-restore-target status --json
```

Preparation uses systemd guards bound to destination machine and policy, retaining
existing unit conditions. Changed policy or machine identity does not inherit
completion. Symlink paths/parents and invalid guards are refused before restore.
After valid completion, status can report a final symlink without following it;
parent directory links remain refused.

### Restore applications into a prepared destination

The destination must have the source architecture, compatible service account
names and numeric UID/GID allocations. Declare stable IDs before fresh
installation: ordinary activation does not renumber existing accounts. The tool
preserves destination machine ID, SSH keys and account databases; never replace
them with the source's databases. Version 2 captures include architecture and
ownership evidence required for restoration; version 1 remains readable for
inventory, verification and source-code recovery.

```sh
nixberry restore rpi-demo-restore --from rpi-demo --backup-access /private/access.json --snapshot RECEIPT_ID --json
nixberry restore rpi-demo-restore --from rpi-demo --backup-access /private/access.json --snapshot RECEIPT_ID --apply --json
```

The context names the original source, even after its declaration is lost.
The default plan evaluates only the destination recipe. Applying checks the
installed identity, generation and policies, then transfers bounded credentials
into private destination tmpfs through verified SSH. Storage/repository pins and
read-only access remain effective. A local repository must also be available
on that destination. Restoration changes files, not disks or filesystem formats.

Allow space for the complete verified payload and publication temporaries in
persistent private `restore.stateDirectory` (default `/var/lib/nixberry-restore`)
outside application paths. Identified ext4 and Btrfs destinations are supported.
Hardlinks spanning incompatible filesystems are refused. Overlay whiteout
metadata is preserved; other device nodes and sockets are refused.

The operation runs in `nixberry-restore`. After controller interruption, inspect
its status/journal on the destination. Retry the **same command** to resume its
identity-bound transaction. Writers stay held until checksums, metadata and
hardlinks are verified and synced. Unexpected destination files are retained
and block completion. Failure during download discards the unpublished stage on
retry; after durable copying begins, the verified stage is reused. Do not remove
transaction records or bypass guards.

After publication, only declared writers start. A service-start failure can be
retried without replacing later application data. Temporary credentials are
removed on normal completion/errors; abrupt termination may leave private tmpfs
state until reboot. Provision the destination's own backup-write credentials
before its schedule can run. Incomplete publication blocks configuration
activation. Verify application behavior, data and a subsequent authorized boot;
then remove `restore.prepare` before ordinary catalogue changes.

Before publication only, a root shell on the verified destination may discard an
unpublished private stage so account configuration can be corrected:

```sh
nixberry-restore-target abort --system "$(readlink -f /run/current-system)" --machine-id "$(cat /etc/machine-id)"
nixberry-restore-target abort --system "$(readlink -f /run/current-system)" --machine-id "$(cat /etc/machine-id)" --apply
```

Abort preserves application paths and refuses transactions already publishing;
resume those with the original restore command.

## Controller providers

This optional interface is for configurations supplying their own backup format
and implementation. Shared Restic setup above needs no target-level provider.
In a machine declaration, for example:

```nix
backup = {
  enable = true;
  provider = "configurations/my-server/backup-provider.py";
  repository = "/home/operator/backups/rpi-demo";
  identityFile = "/home/operator/.local/share/nixberry/backup.agekey";
  recipient = "REPLACE_WITH_PUBLIC_age1_RECIPIENT";
  restoreBackends = [ "rpi" ];
  schedule = "daily";
  keepLast = 7;
  afterUpdate = false;
};
```

The configuration must supply this relative Python provider and matching guest
software; this excerpt alone implements no backup. Providers are trusted local
code, never code loaded from an unauthenticated archive. Runtime repository and
identity paths must be absolute, private, outside the recipe, linked
configurations and Nix store. Never use `builtins.readFile` for private keys.
`restoreBackends` lists only implemented backends. Backup defaults to disabled;
schedule/retention default to `null`, `afterUpdate` to false, and provider
`keepLast` must be at least two.

### Provider commands and write boundaries

`backup`, `backup-stage`, `backup-prune`, `recover-source` and `restore` plan by
default and write with `--apply`. Choose an explicit ID from `backups` for
`verify-backup`, source recovery and restoration; no implicit latest or path
selector is accepted. `restore TARGET --from SOURCE --snapshot ID` requires a
distinct prepared target and a declared source policy/provider, even after source
loss. Neither restore nor source recovery creates a machine, reinstalls, reboots
or activates recovered code.

Provider plans may inspect guests read-only; `--dry-run` skips provider execution
and connections. List/verify/recovery/retention must work without the source;
capture needs a running unlocked source. Full provider verification may write a
private authenticated cache/verification record. Providers own format,
application consistency, identity binding, credential handling and retention;
unsupported variants must fail without weaker fallbacks. Cross-architecture
migration needs an application-specific format, not an assumed portable disk.

### Provider scheduling and monitoring

Use one capture scheduler. A configuration-owned source timer runs independently
of the controller; provider `backup-stage --apply` delegates runtime provisioning.
It captures no application data. `backup.afterUpdate = true;` opts into this action
after a verified update; on partial failure, inspect and repeat staging.
Providers with only a public encryption recipient must distinguish producer
`recorded-captured` from authenticated `recorded-verified` readback.

For controller scheduling, enable the target policy and `backup.schedule`, then:

```sh
nixberry backup-schedule rpi-demo --output /private/backup-units --dry-run
nixberry backup-schedule rpi-demo --output /private/backup-units
```

This writes a new private directory of user units; it installs/enables nothing.
Review before using the normal user service manager. Jobs pin the recipe and
launcher, capture with `--apply`, and prune only after success when configured.
They need verified host trust, unlocked source, a running user manager and SSH
authentication; passing `SSH_AUTH_SOCK` does not copy keys or unlock an agent.

Optional provider monitoring is independent of capture:

```nix
backup.monitor = {
  enable = true;
  maxAgeHours = 36;
  intervalMinutes = 15;
  maxRunMinutes = 90;
  notify = true;
  repeatHours = 24;
};
```

```sh
nixberry backup-check rpi-demo --json
nixberry backup-check rpi-demo --apply --json
nixberry backup-monitor rpi-demo --output /private/monitor-units
```

These values are defaults except `enable` (false). Checks report old/missing
backups, interrupted/failed/overdue captures and unavailable providers. Default
checks are read-only; `--apply` stores protected history and sends configured
local desktop alerts. State lives under
`$XDG_STATE_HOME/nixberry/backup-monitor/` (default `~/.local/state/`) in private
owned directories. PID reuse, partial records and deferred inspection during a
live capture must not appear healthy. Freshness is not cryptographic verification.

`backup-monitor` exports only user check units, enabling no capture, retention or
user lingering. Desktop delivery needs `notify-send` and the user's graphical
notification service, with deduplication, repeat interval and recovery alerts.
Delivery failures are recorded/retried and return nonzero without hiding findings;
this is not email/webhook delivery or proof a person saw the notification.

### Provider protocol

The packaged Linux controller invokes the captured provider with one JSON object
on stdin: `protocol = 1`, `action`, `target`, `source`, `snapshotId`, `output`,
`apply`, `recipe`, normalized `policy`, `targetDeclaration` and pinned
`guestPython`. Automation pins its recipe. The provider may use normal verified
NixBerry SSH operations with unchanged permissions.

Return status zero and `{ "protocol": 1, "ok": true, "result": { ... } }`, or
nonzero and `{ "protocol": 1, "ok": false, "error": "technical_error_code" }`.
`backups` returns `snapshots` with `snapshotId`, UTC epoch `createdAt` and
technical `state`. Other results must distinguish plans, capture, publication
and running restore evidence. Keep stdout JSON and external diagnostics on
stderr; never return secrets, raw private application responses or tracebacks.
The caller reports a localized error once. Use shared `messages.python_script`
for transferred Python. See [provider dispatch](../management/deployment/backups.py).

## Platform scope

Shared capture, Restic maintenance/storage access/restore and the provider
interface cover all five VM backends and Raspberry Pi 5. Image/ISO choice does
not change post-boot backup behavior; offline media alone captures no live data.
Source actions require a running unlocked system. Offline VMs need `access` or
one-time `--ssh`; providers require declared access and reject one-time overrides.
Independent Restic reads survive source loss; restoration needs the prepared
destination. Controllers require Linux, including for Windows hypervisors.

Provider support can be narrower than the common interface. RPi has no VM
lifecycle operations. Builds, isolated checks and storage verification do not
establish physical RPi boot, every hypervisor transport or application recovery.
See [testing](test-plan.md) for separate acceptance procedures.
