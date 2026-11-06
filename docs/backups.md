# Encrypted backups and recovery

[Main guide](../README.md) · [CLI](cli.md) · [Platform contract](platform-consistency.md)

Legacy controller provider policy belongs to a target declaration in `machines/vm/` or
`machines/rpi/`. A configuration supplies a local provider for its
application format, consistency requirements and restore checks. NixBerry
dispatches through that provider using one controller interface for every backend.

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

The shared controller also exposes local capture and native backup creation:

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
operations based on system access without evaluating NixOS. Each native action
checks enablement in the selected configuration when invoked. Completion never
queries remote snapshot inventories or offers legacy backup IDs for local
snapshot removal. Offline bundles carry the configuration and shared tools;
they do not capture application data from stopped systems.

A target with an explicit enabled legacy backup provider retains that provider
for `backup` and `backup-stage`; otherwise these commands use
`nixberry.backup.restic`. Provision native storage credentials and initialize the
repository through the guest tools described below, then use the controller to
prepare and transfer recovery code. The other controller
backup commands in the legacy provider section still require that provider;
use `nixberry-restic` for native inventory and source-independent verification.

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
defaults to `null`, leaving capture manual. The source timer defaults to
`persistent = false`, so missed backups do not cause an unexpected later outage.
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
native captures continue to refuse the stale archive. Updates through other
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
path mapping and recovery archive. The receipt is published only after upload,
manifest/configuration readback and writer recovery succeed. Incomplete payloads
remain in the repository without being listed as completed backups; no automatic
pruning is performed. Successful snapshot-mode capture/upload then removes its
temporary local snapshot. A failed upload retains it for inspection.

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

## Commands and write boundaries

This section describes the legacy provider interface. Native capture and recipe
staging use the configuration-owned commands above.

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

## Retention and user timer bundles

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
Restic timer, is configured on the server instead (see [source-owned automation](#source-owned-automation)).
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

## Source-owned automation

The following provisioning interface applies to target-declared legacy providers.
Native Restic jobs use the shared module and recipe staging described above.

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

## Freshness monitoring and notifications

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
captures are alarms. Legacy captures remain usable without invented run history.
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
Image and ISO installations use the same post-boot interface. Source provisioning
and the optional update hook share that interface across all six target types;
the provider must explicitly implement them. Offline VM declarations
retain local list/verify/recovery/retention commands; capture/restore additionally
require declared live `access`. One-time `--ssh` overrides are not accepted by
backup commands; declare stable access and host trust for automated operations.
RPi is a physical target and gains no VM lifecycle operations.

The controller implementation currently requires Linux, independently of whether
the VM hypervisor is Linux or Windows. Windows controllers are rejected. A
configuration's `restoreBackends` states its implemented subset, not live proof
for every backend. See [testing](test-plan.md) for evidence boundaries.
