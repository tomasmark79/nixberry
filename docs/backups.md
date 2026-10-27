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
it reports declaration policy only. The list is local receipt metadata, not a
new cryptographic proof. `verify-backup` asks the provider for full authentication.
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
Image and ISO installations use the same post-boot interface. Offline VM declarations
retain local list/verify/recovery/retention commands; capture/restore additionally
require declared live `access`. One-time `--ssh` overrides are not accepted by
backup commands; declare stable access and host trust for automated operations.
RPi is a physical target and gains no VM lifecycle operations.

The controller implementation currently requires Linux, independently of whether
the VM hypervisor is Linux or Windows. Windows controllers are rejected. A
configuration's `restoreBackends` states its implemented subset, not live proof
for every backend. See [testing](test-plan.md) for evidence boundaries.
