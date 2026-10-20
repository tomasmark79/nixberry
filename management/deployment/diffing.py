"""Compare recipes and system generations without changing the remote system."""

from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from runtime import status
from updater import build, connection, freeze_recipe, preflight
from messages import _, color_enabled, info, render
from closure_metadata import comparison_command, read_snapshot


# As with Nixon's nvdiffg, select the two highest retained generation numbers.
GENERATION_DIFF = r'''set -eu
generations=$(find /nix/var/nix/profiles -maxdepth 1 -type l -name 'system-*-link' -print | sort -V | tail -n 2)
count=$(printf '%s\n' "$generations" | grep -c '/system-[0-9][0-9]*-link$' || true)
if [ "$count" -ne 2 ]; then
  printf '%s\n' @MISSING@ >&2
  exit 1
fi
previous=$(printf '%s\n' "$generations" | head -n 1)
current=$(printf '%s\n' "$generations" | tail -n 1)
printf @PREVIOUS@ "$previous" >&2
printf @CURRENT@ "$current" >&2
printf @ACTIVE@ "$(readlink -f /run/current-system)" >&2
exec /run/current-system/sw/bin/nvd --color "${NIXBERRY_DIFF_COLOR:-never}" diff "$previous" "$current"
'''


def generation_script(name):
    # Translate and color labels locally; the server only needs its shell and nvd.
    missing = render("error", _("Comparison requires two retained system generations. Use {command} to compare with the recipe."),
                     command=f"nixberry diff {name}")
    replacements = {"@MISSING@": missing}
    for token, message in (
        ("@PREVIOUS@", _("Previous generation: {path}")),
        ("@CURRENT@", _("Newer generation: {path}")),
        ("@ACTIVE@", _("Active system: {path}")),
    ):
        # printf may interpret only our %s placeholder, not percent signs in translations.
        replacements[token] = render("info", message.replace("\\", "\\\\").replace("%", "%%"), path="%s") + "\n"
    script = GENERATION_DIFF
    for token, message in replacements.items():
        script = script.replace(token, shlex.quote(message))
    return script


def compare(recipe, name, target, *, generations=False, dry_run=False):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise RuntimeError(_("Invalid target name for comparison."))
    if target["backend"] not in ("rpi", "libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
        raise RuntimeError(_("Comparison is not implemented for this platform."))
    if not generations and (not recipe or not Path(recipe, "flake.nix").is_file()
                            or not Path(recipe, "flake.lock").is_file()):
        raise RuntimeError(_("Run diff through the nixberry launcher with a complete pinned recipe."))
    info(_("Comparison: {name} ({backend})"), name=name, backend=target["backend"])
    if dry_run:
        info(_("Plan: verify installation → compare the two highest retained generations.") if generations else
             _("Plan: verify installation → build recipe → read closure metadata → nvd diff."))
        info(_("No connection or build. Comparison does not change the remote system."))
        return 0

    if generations:
        snapshot = status(target)
        preflight(target, snapshot)
        command = connection(target, snapshot)
        # SSH without a TTY hides the local terminal from nvd; use local output policy.
        script = generation_script(name)
        if color_enabled(sys.stdout):
            script = "NIXBERRY_DIFF_COLOR=always\n" + script
        return subprocess.run(command + [script], check=False).returncode

    with tempfile.TemporaryDirectory(prefix="nixberry-diff-") as directory:
        frozen = freeze_recipe(recipe, name, target, directory)
        snapshot = status(target)
        previous = preflight(target, snapshot)
        info(_("Running system: {system}"), system=previous)
        info(_("Building the recipe for comparison…"))
        expected = build(frozen, name, directory)
        current_snapshot = status(target)
        if preflight(target, current_snapshot) != previous:
            raise RuntimeError(_("The target system changed during the build. Run diff again."))
        command = connection(target, current_snapshot)
        info(_("Reading closure metadata from the running system and local recipe…"))
        old_metadata = read_snapshot(previous, command)
        new_metadata = read_snapshot(expected)
        if preflight(target, status(target)) != previous:
            raise RuntimeError(_("The target system changed while reading metadata. Run diff again."))
        info(_("New recipe: {system}"), system=expected)
        return subprocess.run(comparison_command(directory, old_metadata, new_metadata), check=False).returncode
