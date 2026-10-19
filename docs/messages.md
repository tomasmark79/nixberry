# User-facing messages and translations

[← Main guide](../README.md)

The shared `management/deployment/messages.py` layer separates text,
translation and rendering. The CLI, diagnostics, offline export, hypervisor
adapters and RPi tools all use it. [AGENTS.md](../AGENTS.md) defines the contract
for future changes.

## Language selection

English is the source and default language, independent of `LANG` and `LC_ALL`.
`NIXBERRY_LANG=cs` selects Czech; `NIXBERRY_LANG=en` selects English.
Unknown language codes and missing translations fall back to English.
The setting also applies to direct tools and locally prepared remote scripts.

Commands, paths, JSON keys, schemas, state codes, check identifiers and technical
values do not change language. Human fields such as `message`, `hint` and `note`
may be translated. Pure Nix evaluation errors and generated declaration comments
remain English. External tools keep their own language.

## Python

```python
from messages import _, info, success, warning

info(_("Transferring the system to {host}…"), host=destination)
success(_("System updated and verified."))
warning(_("The new kernel will be used after a reboot."))
```

Keep text beside the code it explains. Mark complete constant English sentences
with `_()`; never use f-strings or assemble translated fragments inside it.
Substitute named values after translation. For counts, prefer neutral labels
such as `Target count: {count}`; add `ngettext` and tests if grammatical plurals
become necessary.

Translate and format an exception when creating it, but do not also print it:

```python
raise RuntimeError(_("Transfer of target {name} failed. Run {command}.").format(
    name=name, command=f"nixberry status {name}"))
```

The common entry point reports it once through `error()` and exits nonzero.
Describe partial states accurately: activation may have completed even when
SSH confirmation was lost. Insert external exception text as `{error}` so
braces inside that value are never interpreted again.

## Meaning and guidance

- `info`: a current step, plan, context or actionable next command.
- `success`: a verified result, never merely the start of an operation.
- `warning`: a condition needing attention, such as a pending reboot.
- `error`: failure, with known operation state and a useful next step.

Use plain, consistent language. Recovery commands use the actual target and
preserve any `--ssh` endpoint. Failed post-checks must never report verified success.

## Streams, colors and subprocesses

Our diagnostics have a `[nixberry]` prefix, go to stderr and flush immediately.
Data and help go to stdout. In `--json` mode, stdout contains only JSON.
Sending our diagnostics to stderr does not automatically redirect child output.

The prefix uses cyan for progress, green for success, yellow for warnings and
red for errors. Color follows the actual destination stream. `NO_COLOR`,
including an empty value, and `TERM=dumb` disable it. Messages must remain clear
without color.

Forward Nix, SSH, `nvd` and other external output without translation or added
prefixes, including stderr on successful subprocess completion.
Check that subprocess output does not pollute JSON stdout.

`diffg` translates and quotes labels locally, then prints them on remote stderr.
It selects `nvd` colors separately from local stdout; the server needs neither
the Python helper nor a catalogue.

## Bash, PowerShell and transferred Python

Bash uses `messages.sh`, for example `nb_info 'Target: %s' "$name"`.
Formatting and local Czech lookup use only Bash builtins; no Python is spawned
per line. `nb_help` prints help to stdout. Generated `locales/cs.sh` provides
local lookup, including launcher failures.

`shell_script(path)` translates constant messages once, includes the helper and
sets color policy from local stderr. `shell_runner.py` does the same before
local libvirt creation. Guest installation scripts travel over stdin for SSH
and local RPi recovery. Keep printf placeholders unchanged; pass dynamic values
as separate arguments.

`localized_script(path)` translates PowerShell errors in the last literal
`Require` argument and literal `throw`. Quoting is escaped for PowerShell;
conditions and technical values remain untouched.

`python_script(path)` bundles the shared module and selected binary catalogue
with an RPC adapter. Its stdout remains the JSON protocol. Remote hosts need
no project checkout or gettext installation.

| Area | Output contract |
| --- | --- |
| `list/show/status/doctor/cleanup` | Data on stdout; advice/errors on stderr. |
| `new-*` | Declaration preview or path on stdout; confirmation and next step on stderr. |
| `verify/export` | Result/path or JSON on stdout; progress and confirmation on stderr. |
| `ssh/probe/plan` and deployment tools | Preserve technical child output; own diagnostics on stderr. |
| All VM backends | Common Python helper; RPC keeps its technical schema. |
| RPi installation, local recovery, recovery checks | Shared Python/Bash helpers; probes remain data. |
| Image and inventory checks | Diagnostics on stderr; structured results on stdout where applicable. |

Shared Nix sources use `management/messages.nix` and `management/with-locales.nix`.
Adapter `messages.py` symlinks expose the same module during source development.

## Maintain the catalogues

From the recipe root:

```sh
python3 management/deployment/extract_messages.py > management/deployment/locales/nixberry.pot
# Update Czech translations in management/deployment/locales/cs/LC_MESSAGES/nixberry.po.
python3 management/deployment/compile_locales.py
python3 management/deployment/compile_locales.py --check
```

The extraction tool covers marked Python, Bash and PowerShell messages.
The Czech PO contains English `msgid` and Czech `msgstr`. Preserve all named and
printf placeholders. The compile tool requires GNU gettext (`msgfmt`), verifies
coverage and generates the pure Bash table and an ignored development MO file.
If needed, enter `nix shell nixpkgs#gettext` for development.

Nix compiles MO files once during builds using `management/locales.nix` and
attaches the same catalogue to every package. PO and the generated shell table
are versioned; MO binaries are not. POT is not required at runtime.

`test_message_contract.py` checks template freshness and output conventions.
Translation tests cover English defaults, explicit Czech, missing catalogues,
fallback, quoting, remote packaging and substitution order.
Verify terminal/nonterminal streams, JSON, exit codes, `NO_COLOR` and
`TERM=dumb`; failures must preserve child exit codes and must not claim success.
