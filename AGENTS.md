# NixBerry development rules

## Independent repository and local data

- Write source, comments and primary documentation in English. Keep technical identifiers unchanged. Czech is an additional CLI language supplied through gettext. User documentation is English-only.
- Make a local Git commit after each completed, appropriately verified step. Include only related changes and explain the purpose. Push only when explicitly requested.
- `hosts.local.nix`, `admin.pub`, concrete VM files in `instances/` and RPi files in `management/rpi/targets/` are local, ignored data. Preserve local files when removing them from tracking. Version only `.gitignore` and `default.nix` in declaration directories; keep fictional examples separately. Back up local data separately.
- Launchers and exports use `path:` recipes so ignored declarations work without `git add`.
- Nixon independently consumes the local launcher. Do not introduce a dependency on its checkout or a shared lockfile. Documentation or translation work does not authorize changes to live machines.

## Documentation

- The only root entry guide is `README.md`. Details live in `docs/`. Maintain English documentation only; do not add translated guide trees. Keep navigation, source links, anchors and examples valid.
- Use QEMU/KVM (libvirt), Hyper-V, Proxmox VE, VirtualBox, VMware Workstation and Raspberry Pi 5. Backend identifiers and commands remain `libvirt`, `hyperv`, `proxmox`, `virtualbox`, `vmware` and `rpi`.
- Describe current behavior, supported variants and limitations verified against source. Use `vm-demo-*` examples. Do not add migration history, dated results, archives, roadmaps or diagrams. Keep logs and test evidence outside user documentation; the test guide describes procedures and evidence boundaries.

## Consistency across platforms

- Review related behavior across all five VM backends and RPi at every change. Separate shared behavior, intentional exceptions and missing implementations. Explain exceptions and consequences. RPi is not a VM and need not support VM operations.
- Follow the complete feature path: host and instance declarations, defaults, validation, `new-*-vm` scaffolding, capabilities, CLI/aliases, direct tools, adapters, manifests, docs, help and completion. Compare image/ISO, online/offline and supported local/remote operating systems. No backend may weaken permissions, identity verification, dry-run behavior, errors or stdout/stderr separation.
- State the platforms and variants covered, actual verification and remaining gaps before completing a step. Unify accidental differences or explain their reason and impact; help-only claims are not implementation. See [platform consistency](docs/platform-consistency.md).
- Match verification to scope. Static analysis, unit tests and builds do not prove live deployment or boot. Shared contract changes need proportionate checks across backends. These checks do not authorize VM creation, reinstallation, shutdown or removal.

## Recipes and deployment

- Shared shell selection lives in `management/deployment/shell-path.sh`: exported `NIXBERRY_RECIPE` overrides the project directory. Invalid explicit paths fail without fallback. Do not depend on `NIXON_CONFIG_DIR`. Automation explicitly pins its private recipe so interactive settings cannot redirect it. Alias completion delegates implicit arguments to the common CLI completer.
- Named configurations are ordinary NixOS modules under `configurations/`. Each target selects one through `configuration`, defaulting to `default`; `profile` identifies the platform. Configurations explicitly import `modules/common.nix`, may add services/packages and do not import other configurations. Shared behavior belongs in `modules/`; boot, disks, drivers and guest support in `platforms/`; identity, network and resources in declarations. The platform is attached automatically. Do not add lockfiles or separate CLI branches per configuration.
- Prefer usable upstream flake packages over duplicate builds; pin them in the shared recipe lock and use compatible `follows`.
- Configuration selection applies to VM and RPi updates, images, ISOs and export. `--configuration` only creates declarations; edit the stored field for existing targets. Unknown names fail. Check help, completion, manifests and unchanged default system paths. Live tests use isolated recipe copies.
- `allowReset` is an instance-level boolean defaulting to `false`, never a host field or creation-time ownership record. Reset works without evaluating/building systems or media and reads only without `--apply`. Writes require explicit permission, verified owner and actual identity, disks, sharing, snapshots and references. Revalidate after graceful shutdown. Never escalate to forced shutdown or remove uncertain remnants. `cleanup` is a preview; offline and RPi targets have no reset.
- `image` is the default on every VM backend. Optional `iso` installs onto a verified blank disk. `deploymentMethod` stores the default; `--method image|iso` overrides it per operation. `--offline` independently prepares without a hypervisor. RPi uses recovery SD media.
- Deployment changes require both methods in CLI, declarations, completion, plans and export. A method change does not authorize taking over or reinstalling an existing VM. Detach ISO only after successful installation and graceful shutdown. Success includes verified boot of the installed system from disk.

## Messages and translation

- Follow [message conventions](docs/messages.md). Report actual steps and verified outcomes with useful, restrained detail. Explain partial states and actionable recovery; do not imply unverified success.
- English is the source and default language regardless of `LC_ALL`. `NIXBERRY_LANG=cs` selects Czech. Unknown languages and missing translations fall back to English. Never derive the language from the locale used to parse external tools.
- Python uses shared `messages.py`: `_()` around complete constant English sentences; `info`, `success`, `warning`, `error` for stderr. Translate before substituting values. Never put f-strings or assembled sentences inside `_()`. Commands, paths, data keys and technical values are not translated.
- Data, tables and help go to stdout without diagnostic prefixes. Translate human labels; `--json` keeps valid JSON, unchanged schemas and technical values. Forward external tool output unchanged, including successful subprocess stderr.
- Standalone Python tools use shared `ArgumentParser` and `error()` with nonzero status. RPC processes keep JSON; the caller reports the error once.
- Bash uses `nb_info`, `nb_success`, `nb_warning`, `nb_error`, `nb_help` or `fail` with one constant single-quoted English template and separate printf arguments. Do not spawn Python per line. Local shell catalogues use pure Bash; transferred scripts are translated once.
- PowerShell responses remain technical. Use a constant English error as the final `Require` argument or literal `throw`. Never translate conditions or commands.
- Transfer scripts via `shell_script`, `localized_script` or `python_script`. Bundle support and the selected catalogue locally; remote hosts need no gettext installation. New Nix packages include shared sources and `PYTHONPATH` through the common catalogue wrapper.
- Colors follow the destination stream, `NO_COLOR` and `TERM=dumb`, using local TTY policy over SSH. Output remains useful without color.
- Pure Nix evaluation errors and generated source comments are English; do not use runtime translation helpers in pure Nix. External tools retain their own language.
- After message changes, regenerate the POT with `python3 management/deployment/extract_messages.py > management/deployment/locales/nixberry.pot`. Update `locales/cs/LC_MESSAGES/nixberry.po`, then run `python3 management/deployment/compile_locales.py` and `--check`. Nix compiles MO at build time; do not version binaries. Keep complete Czech coverage and matching placeholders. Extraction covers Python, Bash and PowerShell.
- Verify English defaults and explicit Czech, help/errors, data/diagnostic streams, JSON, TTY, `NO_COLOR`, `TERM=dumb`, fallback and remote script packaging. Installation/removal tests stay isolated; a running VM is not permission to reinstall or delete it.
