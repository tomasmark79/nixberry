# Shared platform contract

[← Main guide](../README.md) · [CLI](cli.md) · [Testing](test-plan.md)

Commands and declarations retain the same meaning across VM backends.
Hypervisor-specific differences are in the [platform guide](vm.md).
[AGENTS.md](../AGENTS.md) defines development rules.

## Interface matrix

| Area | libvirt | Hyper-V | Proxmox | VirtualBox | VMware | RPi |
| --- | --- | --- | --- | --- | --- | --- |
| Named configuration | yes | yes | yes | yes | yes | yes |
| Image and ISO | yes | yes | yes | yes | yes | SD recovery |
| `new-*-vm` and `--offline` | yes | yes | yes | yes | yes | separate declaration |
| Target network declaration | yes | yes | yes | yes | yes | yes |
| System and recipe export | yes | yes | yes | yes | yes | yes |
| System management over SSH | yes | yes | yes | yes | yes | yes |
| Online `probe/create/install/provision/reset` | yes | yes | yes | yes | yes | own probe/recovery |
| Default `allowReset = false;` | yes | yes | yes | yes | yes | not applicable |
| English default, optional Czech | yes | yes | yes | yes | yes | yes |
| LUKS image preparation and pinned initrd SSH unlock | yes | yes | yes | yes | yes | not implemented |
| Encrypted ISO installation | rejected | rejected | rejected | rejected | rejected | not applicable |
| Confidential launch and attested key delivery | rejected | rejected | rejected | rejected | rejected | not applicable |

This describes implemented interfaces, not proof of every live combination.
See [encrypted disks](security.md) for the security boundaries and prerequisites.
Offline VMs can gain system management through `access` or `--ssh`, not
hypervisor management. `status` and `doctor` may explain missing access settings.

## Shared behavior

- New declarations use one generator. Resources, networking, configuration,
  methods and permissions have the same meaning on every backend.
- VM default is `image`; `iso` is an equally supported choice.
  `--offline` is independent of the method.
- Direct tool packages provide the same operations. Reset builds separately
  without systems/media, without bypassing permissions or verification.
- Media manifests distinguish `imagePath` and `isoPath`; the unused field is `null`.
- Data goes to stdout and diagnostics to stderr. Successful child stderr is
  retained without translation or added prefixes.
- English source messages use a common Czech catalogue. Language never changes
  JSON schemas, identity checks or technical operations.
- Completion reads fresh local declarations and available operations, without
  cached results, system builds or host queries.
- Concrete declarations are local, ignored data. Launchers and exports use
  `path:` and include them without `git add`.

## Intentional differences

RPi recovers from SD. Libvirt supports local `qemu:///system`;
other adapters define their supported host operating systems and transports.
Disk formats, controllers, agents and storage match each hypervisor.

Hyper-V retains owner field `isoSha256` for prebuilt disks as well.
It identifies stored media and does not change manifest `imagePath`,
`isoPath` or transport `mediaBytes` semantics.

Manual imports do not carry automatic-management markers and public reset
does not adopt them. The HAND VMware runner verifies its own import evidence
and uses internal cleanup. Online managed VMs use public reset.

Pure Nix evaluation errors and generated code comments remain English.
External diagnostics preserve the originating tool's language.

## Review changes

Follow declarations, defaults, validation, scaffolding, capabilities, CLI/aliases,
adapters, export, help and completion. Compare five VM backends, RPi and the
applicable online/offline, image/ISO and host variants.

Separate implementation, isolated tests, builds and live verification.
One passing test does not prove other platforms or every reset protection.
See the [test guide](test-plan.md) for regression and live scenarios.
