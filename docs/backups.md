# Encrypted backups and recovery

[Main guide](../README.md) · [CLI](cli.md) · [Platform contract](platform-consistency.md)

Start with [backup basics in the main README](../README.md#backups) for the
workflow, snapshot terminology and everyday commands. This file is the technical
reference for initial storage setup, application coverage, scheduling, retention
and restore tools. Your selected server configuration's recovery guide supplies
its credentials, service requirements and replacement-machine procedure.

Restic backup and application restoration use shared NixBerry tools. Each
configuration declares its application paths, writer units and storage settings;
it does not implement its own backup or restore program. The selected recipe is
preserved with the backup so recovery can reproduce its source configuration
without the original checkout or device.

Optional controller providers retain a separate format contract. Their
policy belongs to a target declaration in `machines/vm/` or `machines/rpi/`, and
the configuration supplies the provider implementation. NixBerry dispatches
these providers through one controller interface for every backend.

Filesystem, capture method and backup implementation are separate choices:

| Choice | Supported values and meaning |
|---|---|
| Root filesystem (`rootFilesystem`) | `ext4` or `btrfs`; owned by the target declaration and disk layout. |
| Capture method (`nixberry.backup.captureMethod`) | `snapshot` captures read-only Btrfs subvolumes; `cold` reads ext4 or Btrfs application paths while declared writers remain stopped. |
| Backup implementation | Shared NixBerry Restic tools configured through `nixberry.backup`, or a configuration-supplied provider selected by the target's `backup.provider`. |

Restic supports both filesystems. A Btrfs root defaults to `snapshot`, and an
ext4 root defaults to `cold`; Btrfs can also explicitly select `cold`. Local
snapshot commands require Btrfs regardless of the upload method. Shared Restic
application restoration uses the same publication procedure on ext4 and Btrfs
destinations; it restores application files, not a filesystem image or boot disk.
Provider formats and restore capabilities belong to each provider.

## Configuration-owned local snapshots

Configurations importing `modules/common.nix` can enable the shared Btrfs capture
tool. Application paths and writer units belong to that configuration:

```nix
{ nixberryModules, ... }:
{
  imports = [ nixberryModules.common ];
  nixberry.backup = {
    enable = true;
    captureMethod = "snapshot";
    applications.web = {
      paths = [ "/var/lib/web" ];
      stopUnits = [ "web.service" ];
    };
  };
}
```

Replace the example with paths and services actually supplied by the selecting
configuration. The target declaration selects the filesystem with
`rootFilesystem = "btrfs";`; enabling capture does not change an existing disk.
The default capture root is `/`, with private snapshot storage at
`/.snapshots/nixberry`. The standard Btrfs layout keeps snapshot storage and
active swap in sibling subvolumes outside the captured root.

On the installed source, as root:

```sh
nixberry-capture snapshot --json
nixberry-capture snapshot --apply --json
nixberry-capture snapshots --json
nixberry-capture snapshot-remove --snapshot SNAPSHOT_ID --json
nixberry-capture snapshot-remove --snapshot SNAPSHOT_ID --apply --json
nixberry-capture resume --json
nixberry-capture resume --apply --json
```

These guest-side commands capture local filesystem state; they do not upload
data or enable a backup schedule. Planning can
inspect filesystem metadata but changes no files or services. Inventory is
recorded capture metadata, not a fresh integrity check.
The guest tool is shared by all five VM backends and RPi, independently of
image or ISO installation. It requires a running, unlocked source; exported
offline media cannot capture a stopped source's application data.

The shared controller also exposes local capture and Restic backup creation:

```sh
nixberry snapshot vm-demo-server --json
nixberry snapshot vm-demo-server --apply --json
nixberry snapshots vm-demo-server --json
nixberry snapshot-remove vm-demo-server --snapshot LOCAL_CAPTURE_ID --apply --json
nixberry snapshot-resume vm-demo-server --apply --json
nixberry backup vm-demo-server --apply --json
```

These commands require a Linux controller and verified access to a running,
unlocked installed source. All five VM backends and RPi use the same dispatch;
VMs prepared offline need declared `access` or a one-time `--ssh root@HOST:PORT`.
The selected configuration owns `nixberry.backup`, including paths and writer
units; do not duplicate that catalogue in the target declaration.

Creation, removal and recovery only plan without `--apply`; `--dry-run` also
plans local inventory. Plans evaluate and freeze the selected recipe but do not
build systems or contact the source. `snapshots` normally reads the source and
accepts no `--apply`. Executed operations verify existing platform identity,
installation, the exact running system generation and installed policy before
calling the installed guest tool. A different generation or policy fails before
writer shutdown. Local capture IDs contain 32 hexadecimal characters and belong
to that source; they are distinct from Restic completion IDs.

`list`, `show`, completion and exported operation lists advertise candidate source
operations based on system access without evaluating NixOS. Each source action
checks enablement in the selected configuration when invoked. Completion never
queries remote snapshot inventories or offers provider backup IDs for local
snapshot removal. Offline bundles carry the configuration and shared tools;
they do not capture application data from stopped systems.

A target with an explicitly enabled backup provider uses that provider
for `backup`, `backup-stage`, `backup-prune` and `backup-check`; otherwise these
commands use `nixberry.backup.restic`. Provision Restic storage credentials and initialize the
repository through the guest tools described below, then use the controller to
prepare and transfer recovery code. Independent Restic inventory, verification
and recovery of configuration use `--backup-access` as described below.
Controller timer exports and provider monitoring require that provider.

Capture verifies Btrfs source identity, free space, storage placement, mount
coverage and nested subvolumes before stopping writers. Only services active
before capture are restarted. All selected subvolumes are captured read-only
while the configured writers remain stopped. A failed stop, changed source,
timeout or snapshot error prevents successful publication and attempts writer
recovery. A failed restart retains the recovery record and blocks new captures.
`nixberry-capture resume --apply` retries the recorded writers; a boot service
also attempts recovery after a power loss. Never remove a pending recovery
record merely to bypass the capture guard.

Declare every nested data subvolume in `nixberry.backup.roots`, including those
created by container tools. Each entry has `path` and `snapshotDirectory`; its
storage must be on the same Btrfs filesystem outside that source subvolume.
Mounts inside application data need an explicitly captured root. Symbolic-link
directories are rejected; final symbolic-link targets must be included as
explicit application data paths. Capture state and snapshot storage must stay
outside those data paths. An empty `stopUnits` explicitly permits live reads.

For Restic uploads, a separately captured child root supplies that logical
subtree. Its hidden view in ancestor snapshots is excluded, including stale
files underneath bind mounts. Exclusion paths match names literally, including
glob characters and backslashes. Nested paths captured from the same root and
cold captures retain their data without these exclusions.
The catalogue does not discover unlisted applications or unknown writers.
Include any sockets or timers which could restart a writer during capture in
`stopUnits` as well. All configured units are stopped to cancel intervening
activation jobs; only the originally active set is resumed. A detected writer
reactivation prevents successful publication.

Snapshots are retained until explicitly removed. Source writes can retain old
blocks while snapshots exist; `minFreeBytes` is a preflight threshold, not a
space reservation. Removal requires the recorded source/storage paths, source
machine identity, filesystem UUID, snapshot UUID, parent UUID and read-only
state to match. Mounted references visible to the capture process prevent
removal. Failed captures and uncertain remnants are not silently removed.
Snapshot data stays on the source disk, so remote backup is still required for
disk failure. Ext4 remains supported by the disk layout; this local Btrfs
snapshot tool rejects it rather than making a weaker live copy.

## Configuration-owned Restic uploads

The same application catalogue can drive direct encrypted uploads. Enable Restic
in the selecting configuration, for example:

```nix
nixberry.backup.restic = {
  enable = true;
  repository = "sftp:backupagent@nas.example:/NetBackup/vm-demo-server";
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
```

The configuration owns storage and scheduling. No target-level provider or
duplicate application catalogue is needed by these guest tools. `schedule`
defaults to `null`, leaving capture manual. Source timers default to
`persistent = false`, so missed backup or maintenance runs do not catch up later.
Keep one capture scheduler; do not also schedule the controller provider below
for the same data.

Provision a dedicated storage account/key and verified storage host key before
using SFTP. For a nonstandard port the known-host entry uses
`[nas.example]:7922`. Credential and configuration archive files must be regular,
root-owned runtime files outside the Nix store, under directories with trusted
ownership and no untrusted write access. Passwords, SSH private keys and the
recovery archive require private permissions such as `0600`. The host-key and
repository identity files may be publicly readable but not writable by others.
Keep `recoveryFile` on private local storage; Restic encrypts its uploaded copy.

Storage SSH warns by default if the server lacks post-quantum key exchange. For
an explicitly accepted storage endpoint, set
`nixberry.backup.restic.ssh.warnNoPqKex = false;` to suppress only that advisory
on this repository's SFTP probe and Restic connections. Other diagnostics remain
visible. This selects `WarnWeakCrypto=no-pq-kex`; it does not change the key
exchange algorithms, encryption or strict host-key verification. Older clients
which do not recognize `WarnWeakCrypto` ignore that option. Prefer upgrading the
storage server when possible.

The controller creates the sealed recipe archive and binds it to the expected
running system generation and current backup policy:

```sh
nixberry backup-stage vm-demo-server --json
nixberry backup-stage vm-demo-server --apply --json
```

Planning prepares a temporary archive locally and lists its file inventory,
byte size and SHA256 without contacting the source. The archive includes shared
project sources, the selected configuration, its local machine declaration,
the public administrator key and only the selected hypervisor host. Other local
declarations and configurations are excluded. Custom configuration code must
live under the standard project source directories or selected configuration.
Nested symbolic links and private key/secret source files are refused; runtime
credentials stay separate. Archive size is limited to 128 MiB.

Preparation evaluates the selected recipe in pure mode with unchanged lockfiles
and derivation imports disabled. It must reproduce the exact expected system
generation and policies before transfer. Absolute machine-local flake inputs
are refused; retain access to the pinned downloadable inputs for rebuilding.
This check does not activate configuration or read application data.

With `--apply`, the controller verifies the source installation, generation and
installed policy, then sends the archive through strict SSH. The receiver checks
its size, SHA256, file manifest and source/system/policy identity before taking
the capture lock. It replaces the private archive and publishes its binding
atomically while no capture can use it. A failed or interrupted transfer does
not replace the archive. A failure after archive replacement but before binding
publication leaves a stale binding; captures refuse it until staging succeeds.
No services are stopped and no repository is initialized by staging.

A missing or stale binding prevents capture before writer shutdown.
`update TARGET --apply` prepares the selected recovery archive from the same
frozen recipe used to build the system, before transferring or activating it.
After verified activation, it transfers and binds that prepared archive. A recipe
which cannot reproduce the built system prevents activation. Staging failure
after verified activation is reported as a partial update with a retry command;
Restic backups continue to refuse the stale archive. Updates through other
tools and rollbacks require explicit `backup-stage --apply` before capture.
Staging reads a verified archive without evaluating or activating its contents
on the source. The same source-side lock protects snapshot capture, cold capture
and archive publication. NixBerry activation holds the capture locks of both the
installed and replacement configurations, including a changed `stateDirectory`.
An active capture or pending writer recovery refuses activation before changing
the system profile. Capture recovery with no pending writers is a no-op, so the
boot recovery service can run during an update without contending with it.

On the installed source, as root:

```sh
nixberry-restic backup-init --json
nixberry-restic backup-init --apply --json
nixberry-restic backup-stage --system EXPECTED_SYSTEM_PATH --json
nixberry-restic backup-stage --system EXPECTED_SYSTEM_PATH --apply --json
nixberry-restic backup --json
nixberry-restic backup --apply --json
nixberry-restic backups --json
nixberry-restic verify-backup --snapshot SNAPSHOT_ID --json
```

Initialization is explicit and separate from capture. It requires a new identity
pin file, initializes a new repository and writes its returned ID privately.
It refuses an existing pin and does not adopt an existing repository. Ordinary
capture never initializes storage. Both SFTP host trust/authentication and the
pinned repository ID are checked before writer shutdown. No SSH password prompt,
agent fallback or automatic host-key acceptance is used. Absolute local
repositories are also supported for isolated testing; other Restic storage
backends are not accepted by this interface.
Replace the staging example with the complete store path of the system expected
from the recipe that produced the sealed archive. Existing-file staging also
validates that archive's manifest and identity. The tool compares it with the actual
`/run/current-system`; supplying an unrelated generation fails without publishing
the sidecar.

`captureMethod` defaults to `snapshot` for a Btrfs root and `cold` for ext4.
Snapshot mode captures read-only subvolumes and resumes writers before upload.
The capture lock prevents explicit removal during upload. Cold mode reads the
configured paths directly with their writers stopped until upload and consistency
checks finish, then restores the original active set. Ext4 needs no conversion
and neither mode creates a local application data staging copy.

Restic encrypts before storage transfer. Cache is disabled and temporary pack
files use a private tmpfs runtime directory. The source service limits that
filesystem to 512 MiB and its memory cgroup to 1 GiB with disk swap disabled.
`timeoutSeconds` defaults to 3600 and bounds all Restic commands in one operation;
writer recovery has its own timeout. Abrupt failures retain the same durable
writer recovery record as local captures, and the source service also invokes
recovery after stopping. The job is not restarted by a configuration switch.

A completed backup has an encrypted payload snapshot and a small encrypted
completion receipt. The payload includes application data, the logical source
path mapping and recovery archive. New captures also include the source system
architecture and account names with numeric UID/GID values inside the encrypted
manifest. This evidence excludes passwords, login settings and group membership;
it is not an account database to install on the destination. Numeric file owners
without named source accounts remain possible, for example with containers.
Account lookups run before writer shutdown. Version 1 captures remain readable
for inventory, verification and recovery of the configuration, but lack this
architecture and ownership evidence. The receipt is published only after upload,
manifest/configuration readback and writer recovery succeed. Incomplete payloads
remain in the repository without being listed as completed backups and are not
candidates for Restic retention. Successful snapshot-mode capture/upload then
removes its temporary local snapshot. A failed upload retains it for inspection.

The public `snapshotId` is the full 64-character Restic completion receipt ID;
it differs from the 32-character local Btrfs capture ID. `recorded-captured`
means the producer completed and the small recovery bindings were read back.
`verify-backup` streams the entire referenced payload through Restic to
`/dev/null` and checks the recovery archive hash before reporting
`recorded-verified`. It does not create a restored application tree.
Verification can run on another Linux recovery host with the original policy,
repository pin/password and separately authorized SFTP access; the source need
not be running, and its data/recovery archive need not exist locally. Supply a
private tmpfs runtime directory on that host. Restic uses a decryption-capable
repository key on the source; use separate repositories and storage permissions
for sources that must not share decryption access.

These guest tools use the same implementation on all five VM backends and RPi.
Image/ISO choice and the host hypervisor OS do not change their post-boot
interface. Capture requires a running, unlocked source; verification reads the
repository independently. Hypervisor boot, ARM hardware and application recovery
need their own validation.

### Restic maintenance policy

The selected configuration also owns optional retention and freshness settings:

```nix
nixberry.backup.restic = {
  keepLast = 7;
  maxAgeSeconds = 129600;
  pruneSchedule = "*-*-* 05:00:00";
  checkSchedule = "hourly";
};
```

| Option | Default | Meaning |
| --- | --- | --- |
| `keepLast` | `null` | Retain 1–100000 distinct completed payloads for the current source machine; `null` disables Restic pruning. |
| `maxAgeSeconds` | `null` | Maximum authenticated capture age, 1–31536000 seconds; `null` disables source freshness checks. |
| `pruneSchedule` | `null` | Optional systemd calendar; requires `keepLast`. |
| `checkSchedule` | `null` | Optional systemd calendar; requires `maxAgeSeconds`. |

Enabling Restic publishes the separate `/etc/nixberry/maintenance.json` policy:
`{ "version": 1, "keepLast": null, "maxAgeSeconds": null }` with configured
thresholds substituted. It is also available as
`system.build.nixberryBackupMaintenancePolicy`. The existing backup v1 JSON
schema in `/etc/nixberry/backup.json` is unchanged.

A configured threshold provides the root-only `nixberry-backup-prune.service`
or `nixberry-backup-check.service`; its timer exists only with the matching
schedule. All four options default to `null`, leaving maintenance disabled.
Timers share `persistent` with backup scheduling. Services use private runtime
storage and the Restic timeout/memory limits, and leave application writers
running. Pruning is ordered after backup and capture recovery; freshness checks
are ordered after backup. Prepared restore destinations hold enabled maintenance
services and timers until restoration completes.

### Restic retention

```sh
nixberry backup-prune vm-demo-server --dry-run --json
nixberry backup-prune vm-demo-server --json
nixberry backup-prune vm-demo-server --apply --json
```

`--dry-run` evaluates the selected policy without contacting the source or
storage. Without either mode flag, the controller verifies the source generation
and policies and reads the repository to return the actual retention plan.
The source must be running and unlocked. Applying verifies the generation and
current machine identity again under the capture lock, and refuses pending
writer recovery. No application services are stopped.

Retention authenticates every source completion receipt and its payload manifest.
Duplicate receipts for one payload count as one capture. Only the current source
machine's groups are eligible; other machine identities remain untouched.
Before any deletion, it fully verifies the newest retained payload and its sealed
recipe, then rechecks the inventory, generation and machine identity. It deletes
only the explicitly selected old receipt and payload IDs with Restic
`forget --prune`. It does not run repository unlock or delete incomplete or
unknown snapshots. A preview authorizes no deletion.

On the source, the equivalent commands are
`nixberry-restic backup-prune --json` and
`nixberry-restic backup-prune --apply --json`. They read `--config` from
`/etc/nixberry/backup.json` and `--maintenance` from
`/etc/nixberry/maintenance.json` by default.

### Restic freshness checks

```sh
nixberry backup-check vm-demo-server --json
nixberry backup-check vm-demo-source --backup-access /private/access.json --max-age-seconds 129600 --json
```

The source-owned check uses configured `maxAgeSeconds` and the current source
machine identity. Its service runs `nixberry-restic backup-check --json`; an
unhealthy result fails the systemd unit and is available in its journal. Restic
checks send no desktop notifications and do not accept `--apply`.

Independent `--backup-access` checks require an explicit `--max-age-seconds`
within the same bounds. They need neither the source nor its declaration and
select the newest authenticated capture for the preserved `sourceId`, including
across source machine replacements. `--dry-run` skips storage access.

Results use `status = ok|stale|missing|future`, with the selected receipt,
capture timestamp, age and threshold when available. A healthy check returns
`0`; an unhealthy result or operational error returns `1`. Checks authenticate
receipt/manifest metadata and the selected sealed recipe; they do not read all
application payload files. Use `verify-backup` separately for full payload
authentication. Invalid metadata fails the check instead of falling back to an
older apparently healthy capture.

On the source, `nixberry-restic backup-check --statistics --json` additionally
reports `statistics.restorePoints` and `statistics.latestLogicalBytes`. The
count covers distinct completed payloads authenticated for the current machine;
unfinished uploads and backups of previous machine identities are excluded.
The size uses Restic's `restore-size` mode on the selected full payload ID and
includes its sealed recovery recipe and manifest. It is logical restored size,
not repository disk usage or full payload verification. A missing backup reports
zero points and a null size. Statistics errors fail the check; ordinary checks
retain their existing JSON schema and avoid this additional metadata traversal.

## Controller providers

```nix
backup = {
  enable = true;
  provider = "configurations/my-server/backup-provider.py";
  repository = "/home/operator/backups/vm-demo-server";
  identityFile = "/home/operator/.local/share/nixberry/backup.agekey";
  recipient = "REPLACE_WITH_PUBLIC_age1_RECIPIENT";
  restoreBackends = [ "libvirt" ]; # Only backends actually implemented by this provider.
  schedule = "daily";
  keepLast = 7;
  afterUpdate = false; # Opt in only when the provider implements backup-stage.
};
```

Backup is disabled by default. `provider` is a relative Python path within a
configuration. The public recipient is an X25519 age recipient. Repository and
identity paths are absolute runtime strings outside `/nix/store`; never use
`builtins.readFile` or a Nix path for the private identity. The provider must enforce
private ownership/modes, secret handling, source identity and application consistency.
The controller policy is metadata; images, manifests and exported recipes do not
include private key contents or backup data. Preserve local declarations separately.
Both runtime paths must also remain outside the recipe and linked configurations;
the dispatcher rejects those locations, including resolved directory links.

`afterUpdate` defaults to false. When enabled, a successful `update --apply`
also calls the provider's `backup-stage --apply` action to refresh source runtime
access and encrypted recovery configuration. A failed provisioning step returns
an error after the system update; inspect it and rerun `backup-stage --apply`.
Read-only updates never provision backup access.

`schedule` and `keepLast` default to `null`. `keepLast` must be at least two when
set. Editing these fields does not install jobs or remove backups. The selecting
configuration must also enable its guest-side exporter with the matching public
recipient; a controller declaration does not deploy guest software.

## Prepared restore destinations

The destination configuration can hold its writers before first boot:

```nix
nixberry.backup = {
  enable = true;
  restore.prepare = true;
  applications.web = {
    paths = [ "/var/lib/web" ];
    stopUnits = [ "web.service" "web.socket" ];
  };
  applications.live-files = {
    paths = [ "/var/lib/live-files" ];
    stopUnits = [ ];
    restoreUnits = [ "file-consumer.service" ];
  };
};
```

Keep the application definitions in configuration-owned shared modules. A
destination configuration imports those modules and `modules/common.nix`; it
does not import another named configuration. Select it through the ordinary
stored `configuration` field. The same mode applies to all five VM backends,
image/ISO installation, offline artifacts and RPi target systems.

`restoreUnits` defaults to a nonempty `stopUnits` list. Applications captured
without writer shutdown must declare `restoreUnits` explicitly; `[]` declares
that there are no managed destination writers. Include activating sockets and
timers. Reserved SSH, networking and management unit names and prefixes are
rejected. DNS application services can be held on a destination even when they must remain live during
source capture. Source-owned Restic backup services/timers and enabled Restic
prune/check services/timers are held as well.
The management network and storage access must work independently of held
application services.

Preparation adds systemd drop-ins, preserving original service definitions,
aliases, template instances and existing conditions. Completion paths include
a hash of the destination policy and its machine ID. State defaults to the private
root-owned `/var/lib/nixberry-restore`, outside application data. A changed policy
or a new machine identity does not inherit another completion namespace.

On a booted, prepared destination:

```sh
nixberry ssh vm-demo-restore -- nixberry-restore-target status --json
```

The installed helper requires root and reports readiness without changing units,
data or completion files. It checks architecture, an optional expected `--system`
generation, loaded guard contents and effective conditions, daemon-reload state,
writer inactivity and empty/absent application paths. Existing files, populated directories or active
writers are not ready. Before completion, symbolic-link paths and parent
traversal are refused. After valid completion, status reports a final symbolic
link as present and nonempty without following its target; symbolic-link parent
directories remain refused. Invalid completion records are refused. Its policy
contains destination paths and writers, without restoring system accounts,
machine identity or SSH keys.

The `status` operation inspects the destination without publishing data or
starting services. Restic data publication uses
[`restore --backup-access`](#restore-applications-into-a-prepared-destination).

## Independent Restic storage access

Keep a private access context, repository password and authorized SSH credentials
on the recovery workstation, outside every NixBerry recipe and the Nix store.
These files must survive loss of the source. The context contains storage access
and source identity, without duplicating the source's application catalogue:

```json
{
  "version": 1,
  "target": "vm-demo-source",
  "sourceId": "libvirt:EXAMPLE_UUID",
  "repository": "sftp:recovery@nas.example:/backup/vm-demo-source",
  "repositoryId": "1111111111111111111111111111111111111111111111111111111111111111",
  "passwordFile": "/home/operator/.local/share/nixberry-recovery/password",
  "ssh": {
    "port": 22,
    "identityFile": "/home/operator/.local/share/nixberry-recovery/ssh-key",
    "knownHostsFile": "/home/operator/.local/share/nixberry-recovery/known_hosts"
  },
  "runtimeDirectory": "/run/user/1000/nixberry-recovery",
  "timeoutSeconds": 3600
}
```

Replace the fictional `sourceId` with the value from the source's
`/etc/nixberry/backup.json`, and `repositoryId` with the complete ID from its
configured `repositoryIdFile`. Preserve these pins before losing the source.
The target must match the archive's original declaration name. Use the recovery
operator's local paths and UID; `runtimeDirectory` must reside on tmpfs. For a
local repository use its absolute path and `"ssh": null`.

Store the context and private credentials with mode `0600`, owned by the user
running recovery. Parent directories need trusted ownership and no untrusted
write permissions; symbolic links are refused. Root is not required for these
operations. A distinct SSH key can use read-only SFTP access to the same pinned
repository. The reader uses Restic `--no-lock` and permits only identity,
inventory and payload reads; it cannot initialize, capture, unlock or prune.
SSH host keys remain explicit, with no agent or automatic trust fallback.
Metadata uses bounded tmpfs files; full verification streams application data
to discard without a local data copy.

Independent verification and restoration have no reader lease with read-only
SFTP credentials. The storage operator must coordinate them with scheduled or
manual pruning so that referenced snapshots remain available throughout a read.
The source capture lock does not cover independent readers on another machine.

```sh
nixberry backups vm-demo-source --backup-access /private/access.json --json
nixberry verify-backup vm-demo-source --backup-access /private/access.json --snapshot RECEIPT_ID --json
nixberry recover-source vm-demo-source --backup-access /private/access.json --snapshot RECEIPT_ID --output /home/operator/recovered-source
nixberry recover-source vm-demo-source --backup-access /private/access.json --snapshot RECEIPT_ID --output /home/operator/recovered-source --apply
```

`RECEIPT_ID` is a complete 64-character Restic receipt ID from inventory.
The context explicitly selects Restic storage reads, including when a
provider exists. Shared management file aliases are materialized as regular
source files; external or directory aliases remain refused. The selected recipe
must still reproduce the original source generation and policy exactly. No source connection, NixOS evaluation or source declaration is
needed. Any available NixBerry checkout can supply the controller. These
operations require Linux; all five VM backends and RPi use the same storage
contract. Inventory operation lists are candidates and do not evaluate Restic
enablement or inspect access contexts.

An independent access context may include `"warnNoPqKex": false` in its `ssh`
object for the same storage-specific advisory exception. Omitting the field
keeps warnings enabled. The setting also accompanies credentials transferred to
a prepared restore destination; it does not affect management SSH connections.

### Restore applications into a prepared destination

Restic restoration uses a distinct installed destination with the same source
architecture (`aarch64-linux` or `x86_64-linux`). Declare its own hostname, network
and hardware or VM identity. Select the required service configuration and enable
`nixberry.backup.restore.prepare = true` before installing or updating that target.
Preparation holds the configuration's `restoreUnits`; it does not stop a running
production installation for takeover. Keep account names and the numeric IDs used
by application files, ACLs and capabilities compatible with the archived source.
The tool preserves the destination's machine ID, SSH keys and account databases.
Declare stable service UID/GID values before a fresh installation. Ordinary NixOS
activation does not renumber existing accounts; a destination with incompatible
allocations needs a fresh preparation or a separate account migration before
application restoration. Never install the source's account databases over it.

```sh
nixberry restore vm-demo-destination --from vm-demo-source --backup-access /private/access.json --snapshot RECEIPT_ID --json
nixberry restore vm-demo-destination --from vm-demo-source --backup-access /private/access.json --snapshot RECEIPT_ID --apply --json
```

The access context still names `vm-demo-source`, including when that declaration
and the original machine no longer exist. Explicit `--backup-access` selects this
Restic path; ordinary provider restoration retains its existing contract.
The default plan evaluates the destination recipe without contacting either
machine or storage. Applying checks the installed generation, declared identity
and exact policies before transferring bounded credentials through SSH stdin into
private destination tmpfs. Storage host and repository pins and read-only SFTP
access remain effective. A local repository path must also be available on the
destination. Restic controller restoration currently requires Linux and uses the
same path for all five VM backends and Raspberry Pi; it does not install disks or
convert filesystems.

The applied operation runs in the managed `nixberry-restore` system unit. Its
control group contains transfer children, so failure of the main process cannot
leave a publisher running after its locks are released. Inspect
`systemctl status nixberry-restore` and its journal when controller access is lost.

A new transaction requires empty application paths; existing directory scaffolding
must match the selected backup. The destination needs writable identified Btrfs or
ext4 filesystems, including persistent private `restore.stateDirectory` outside
application data. Allow temporary capacity there for the complete verified
payload and temporary files used during cross-filesystem publication. The default
is `/var/lib/nixberry-restore`; declare another suitable persistent volume when
needed. The original source never receives this restoration staging copy.
Same-filesystem hardlinks avoid a second full copy where possible. A source
regular-file hardlink group spanning incompatible destination filesystems is
refused. Publication preserves character-device `0:0` overlay whiteouts as
metadata without reading a device. Other character or block devices and sockets
are refused.

The private transaction records the exact source receipt, destination machine,
generation, policy and filesystem layout. A retry with the same command resumes
an interrupted transaction. Before the durable copying phase, the unpublished
private stage is discarded and downloaded again, including after an interrupted
report write. After that phase begins, the verified stage is reused. Writers
remain held; checksums, metadata and regular-file hardlink
relationships are verified and synced before durable completion. Unexpected
additional destination files are retained and prevent completion. No account,
SSH, boot or other operating-system identity paths can be replaced. Restic
application restoration requires version 2 capture evidence; version 1 remains
available for inventory, verification and configuration recovery.

After data completion, the command starts only the declared application writer
units. A failed service start can be retried with the same command without
replacing subsequent application data. Provision the destination's own producer
credentials before enabling its backup schedule; temporary restore credentials
are not installed as future backup write access. Normal completion or errors
remove transferred credentials; abrupt termination can leave private tmpfs state
until reboot. Incomplete publication blocks configuration activation. Once the
restored applications are verified, remove `restore.prepare` before ordinary
changes to the restore application catalogue.

If failure occurred before publication, inspect the transaction on the verified
destination. The target helper can discard only an unpublished private stage,
leaving application paths untouched, so account configuration can then be fixed:

```sh
nixberry-restore-target abort --system "$(readlink -f /run/current-system)" --machine-id "$(cat /etc/machine-id)"
nixberry-restore-target abort --system "$(readlink -f /run/current-system)" --machine-id "$(cat /etc/machine-id)" --apply
```

Run these commands as root on the destination. Abort refuses transactions that
have entered publication; retry their exact restore request instead. Do not
manually delete transaction state or bypass the writer guards.

Source recovery plans without storage access by default; `--apply` retrieves the
authenticated recipe and writes a new private output directory. It checks the
receipt, manifest, archive hash, sealed file manifest and source/generation/policy
identity before writing. The result contains `recovery.json` and `recipe/`.
Existing output is refused. An interrupted extraction retains a partial
directory with `INCOMPLETE`; inspect it and choose a new output for a retry.
Recovered code is never evaluated or activated. Source recovery alone does not
restore application data; Restic publication into a replacement is separate.
`--dry-run` skips storage access for inventory, verification, freshness and source
recovery.

## Provider commands and write boundaries

This section describes the provider interface. Shared capture and recipe
staging, maintenance and independent storage reads use the commands above.

```sh
nixberry backup vm-demo-server --json
nixberry backup vm-demo-server --apply --json
nixberry backups vm-demo-server
nixberry verify-backup vm-demo-server --snapshot SNAPSHOT_ID --json
nixberry recover-source vm-demo-server --snapshot SNAPSHOT_ID --output /private/recovered --apply
nixberry restore vm-demo-restore --from vm-demo-server --snapshot SNAPSHOT_ID --json
nixberry restore vm-demo-restore --from vm-demo-server --snapshot SNAPSHOT_ID --apply --json
```

`backup`, `restore`, `recover-source` and `backup-prune` plan by default and write
only with `--apply`. Capture/restore plans can inspect a guest read-only.
`--dry-run` avoids provider execution, guest connections and readiness checks;
it reports declaration policy only. The list is recorded repository metadata,
not a new cryptographic proof. Providers may query remote storage without
contacting the source. `verify-backup` asks the provider for full authentication
and may publish a protected local cache and a verification record on the repository.
List, verification, source recovery and retention must work without the source
machine; capture requires a running, unlocked source.

Choose an explicit ID from `backups`; paths or an implicit latest backup are not
accepted as snapshot selectors. `--from` selects a declared source policy, not a
source machine to contact during recovery. The source declaration and provider
must be available on the controller, even when the original hardware is gone.

Restoration uses a distinct, already prepared target and only declared supported
backends. Providers must refuse populated or uncertain destinations, preserve
management trust, bind writes to the actual target identity and publish only after
complete authenticated transport. The CLI never creates/reinstalls a target,
activates recovered configuration or reboots as a side effect of `restore`.
Those remain separate, reviewed normal deployment operations. Successful data
publication is not proof of a running recovery; require application/data checks
and a verified boot afterward.

Providers can report narrower architecture, backend or baseline requirements.
An unsupported variant must fail without selecting a weaker fallback. Restoring
a cold filesystem between architectures requires an application-specific migration
format rather than treating the filesystem as portable.

## Provider retention and user timer bundles

This section applies to enabled target-level providers. Configuration-owned
Restic uses [Restic retention](#restic-retention) and its source system timers.

```sh
nixberry backup-prune vm-demo-server --json
nixberry backup-prune vm-demo-server --apply --json
```

The retention plan names the exact candidates. The provider must authenticate
retained recovery copies, revalidate candidates and preserve partial captures,
unrelated data and snapshots currently used by another operation. Pruning is
separate from capture and is not authorized by a plain plan or list command.
Configuration-owned formats define additional rules such as retaining copies
separately for each source machine identity.

Choose where backups are scheduled: `backup-schedule` prepares user units on
the controller running NixBerry. A server-owned scheduler, such as a NixOS
Restic timer, is configured on the server instead (see [provider automation on the source](#provider-automation-on-the-source)).
Keep one capture scheduler; this command does not inspect or change a server's timer.

For controller scheduling, enable `backup.enable` and set `backup.schedule` in
the target declaration. Both the target name and `--output` are required:

```sh
nixberry backup-schedule vm-demo-server --output /private/backup-units --dry-run
nixberry backup-schedule vm-demo-server --output /private/backup-units
```

`backup-schedule` validates the calendar and writes a private, new directory with
a systemd user service/timer. Like `export`, it writes locally by default;
`--dry-run` writes nothing. It installs and enables nothing. The service pins the
absolute recipe/launcher, applies capture and, when `keepLast` is set, runs pruning
only after successful capture. Inspect the files and retention policy before
installing/enabling them with the normal user service manager.

Noninteractive jobs need the same pinned host trust and available SSH authentication
as interactive capture. The bundle passes `SSH_AUTH_SOCK` from the user manager;
it does not copy SSH keys or unlock an agent. User-manager/agent availability,
source unlock and a separate recovery copy of the age identity must be arranged
before relying on scheduled operation. Monitoring or automatic timer activation
is not performed by bundle export.

## Provider automation on the source

The following provisioning interface applies to target-declared providers.
Restic jobs use the shared module and recipe staging described above.

A configuration may install its own system service and timer to push encrypted
backups directly to remote storage. This runs inside the NixOS source, on either
a VM or RPi; it does not depend on a controller being online. The configuration
owns the schedule, consistency protocol, storage transport and retention rules.
The generic `backup-schedule` command continues to export controller user units.
Choose one capture scheduler to avoid competing jobs.

```sh
nixberry backup-stage vm-demo-server --json
nixberry backup-stage vm-demo-server --apply --json
```

`backup-stage` plans by default. `--apply` delegates runtime provisioning to the
configuration's provider; it does not capture application data. A provider may
generate a dedicated source SSH identity, enroll only its public key with a
restricted storage receiver, pin storage host keys and copy an encrypted recovery
bundle to the source. These are provider requirements, not automatic features of
every configuration. The private decryption identity remains with the recovery
operator, outside the source and build artifacts. Refresh provisioning after
configuration changes, manually or with `backup.afterUpdate = true;`.

Source-only captures can verify producer completion and transport checksums while
holding only the public encryption recipient. Such records must be labeled
`recorded-captured`; they are distinct from `recorded-verified` authenticated
readbacks. Use `verify-backup` with the operator identity for full archive and
application validation. Providers must bind data and recovery configuration
inside authenticated encrypted contents before accepting a remote recovery copy.

## Provider freshness monitoring and notifications

This section describes provider monitoring. Restic freshness uses the
[source-owned or independent check](#restic-freshness-checks) above, without the
provider report history or desktop notification path.

The controller can check repository metadata without contacting the source or
reading its decryption identity. Enable a separate monitoring policy:

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

These values are defaults except `enable`, which defaults to false. Monitoring is
independent of the capture schedule. Exporting monitoring units does not enable
capture or retention jobs, start a VM or modify guest data.

```sh
nixberry backup-check vm-demo-server --json
nixberry backup-check vm-demo-server --apply --json
nixberry backup-monitor vm-demo-server --output /private/monitor-units
```

`backup-check` is read-only by default; `--dry-run` skips provider execution.
`--apply` records a protected report and sends configured desktop notifications.
An actionable alarm or failed notification delivery returns nonzero with valid
JSON. `backup-monitor` exports reviewable systemd user units; it never installs or
enables them. Its timer first checks two minutes after activation, then at the
configured interval with bounded jitter. The service only runs `backup-check`.

Checks cover missing/old recorded snapshots, invalid/partial captures,
provider unavailability and the last recorded capture attempt. Source-owned
providers may return source run history without a local process ID; its duration
is still bounded by `maxRunMinutes`. Reports distinguish producer/transport
checks from authenticated readback. Applied CLI captures
atomically record start, successful/failed finish, duration, process identity and
the last successful run time. Interrupted owners/PID reuse and overdue running
captures are alarms. Previously recorded captures remain usable without invented run history.
Repository contention during a known live capture defers inventory inspection;
a cached stale backup still alarms, and runtime limits bound deferral. A deferred
report is `running`, not a fresh integrity proof.

Reports/run history live in owned mode `0700` directories under
`$XDG_STATE_HOME/nixberry/backup-monitor/` (default `~/.local/state/`), separated by
recipe/target/repository identity. Files are mode `0600`; unsafe links, malformed
state and unknown run fields are refused. State is separate from the repository,
so a missing backup mount cannot erase its failure history. No private key contents,
application responses or raw error text are persisted.

Desktop alerts are sent locally through `notify-send` and the user's notification
service. Repeated unchanged alarms are suppressed until `repeatHours`; a new failed
run or changed alarm alerts again. A return to healthy state sends a recovery
notification. Delivery failure is recorded and retried, without hiding the backup
finding. A graphical/user bus must be available; this is not remote email/webhook
delivery. Receipt freshness is not new cryptographic authentication: run
`verify-backup` separately to detect ciphertext corruption.
Periodic checks require the controller's user manager to be running. Export does
not enable user lingering or install a system-wide service.

## Provider protocol

The provider is captured with the configuration and run using the packaged Python,
age, zstd, GNU tar, Git, Nix, SSH and systemd tools on a Linux controller.
Its stdin is one JSON object: `protocol = 1`, `action`, `target`, `source`,
`snapshotId`, `output`, `apply`, `recipe`, normalized `policy`, `targetDeclaration`
and a pinned `guestPython` path. Automation pins the explicit original recipe;
interactive recipe settings cannot redirect it. Providers may use ordinary
NixBerry SSH/verify operations, with the same identity and permissions.

Reply on stdout with `{ "protocol": 1, "ok": true, "result": { ... } }` and
status zero. A failure returns nonzero and
`{ "protocol": 1, "ok": false, "error": "technical_error_code" }`.
The caller reports a localized error once; no passwords, private keys, raw
application responses or tracebacks belong in replies. External tool diagnostics
remain unchanged on stderr. Transferred Python RPC scripts use the common
`messages.python_script` packaging helper. Providers are trusted local configuration
code and must never be loaded from an unauthenticated application archive.

`backups` returns `snapshots` with `snapshotId`, `createdAt` (UTC epoch) and technical
`state`. Other result objects are provider-specific and must distinguish plan,
capture verification, publication and running restore evidence.

## Platform scope

The declaration, CLI, completion and exported metadata are shared across QEMU/KVM
(libvirt), Hyper-V, Proxmox VE, VirtualBox, VMware Workstation and Raspberry Pi 5.
Image and ISO installations use the same post-boot interface. Restic retention
and source freshness use the same configuration, generation checks and guest
tools on all six target types, for Btrfs and ext4 captures. They require a running,
unlocked source; offline VM declarations need `access` or a one-time `--ssh`
override. Independent Restic storage reads and restore credentials survive source
loss through `--backup-access`; destination restoration still needs live access
to the prepared replacement.

Provider source provisioning and the optional update hook share that interface
across all six target types; the provider must explicitly implement them.
Provider offline declarations retain local list/verify/recovery/retention commands;
capture/restore additionally require declared live `access`. Provider backup commands
do not accept one-time `--ssh` overrides. Declare stable access and host trust for
automated operations.
RPi is a physical target and gains no VM lifecycle operations.

The controller implementation currently requires Linux, independently of whether
the VM hypervisor is Linux or Windows. Windows controllers are rejected. A
configuration's `restoreBackends` states its implemented subset, not live proof
for every backend. See [testing](test-plan.md) for evidence boundaries.
