# Encrypted backups and recovery

[Main guide](../README.md) · [CLI](cli.md) · [Platform contract](platform-consistency.md)

Backup policy belongs to a target declaration in `instances/` or
`management/rpi/targets/`. A configuration supplies a local provider for its
application format, consistency requirements and restore checks. NixBerry
dispatches through that provider using one controller interface for every backend.

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
nixberry backup-schedule vm-demo-server --output /private/backup-units --dry-run
nixberry backup-schedule vm-demo-server --output /private/backup-units
```

The retention plan names the exact candidates. The provider must authenticate
retained recovery copies, revalidate candidates and preserve partial captures,
unrelated data and snapshots currently used by another operation. Pruning is
separate from capture and is not authorized by a plain plan or list command.
Configuration-owned formats define additional rules such as retaining copies
separately for each source machine identity.

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
