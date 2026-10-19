"""Porovnání receptu a systémových generací bez změny vzdáleného systému."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from runtime import status
from updater import build, connection, freeze_recipe, preflight
from messages import _, color_enabled, info, render


# Stejně jako nvdiffg na Nixonu vybíráme dvě nejvyšší dochovaná čísla generací.
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
    # Věty přeložíme a obarvíme místně; server potřebuje pouze svůj shell a nvd.
    missing = render("error", _("Pro porovnání jsou potřeba dvě dochované systémové generace. "
                               "Pro porovnání s receptem použij {command}."),
                     command=f"nixberry diff {name}")
    replacements = {"@MISSING@": missing}
    for token, message in (
        ("@PREVIOUS@", _("Předchozí generace: {path}")),
        ("@CURRENT@", _("Novější generace: {path}")),
        ("@ACTIVE@", _("Aktivní systém: {path}")),
    ):
        # printf smí interpretovat pouze náš zástupný %s, nikoli procenta z překladu.
        replacements[token] = render("info", message.replace("\\", "\\\\").replace("%", "%%"), path="%s") + "\n"
    script = GENERATION_DIFF
    for token, message in replacements.items():
        script = script.replace(token, shlex.quote(message))
    return script


def compare(recipe, name, target, *, generations=False, dry_run=False):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise RuntimeError(_("Neplatné jméno cíle pro porovnání."))
    if target["backend"] not in ("rpi", "libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
        raise RuntimeError(_("Porovnání této platformy není implementované."))
    if not generations and (not recipe or not Path(recipe, "flake.nix").is_file()
                            or not Path(recipe, "flake.lock").is_file()):
        raise RuntimeError(_("diff spusť přes spouštěč nixberry s úplným připnutým receptem."))
    info(_("Porovnání: {name} ({backend})"), name=name, backend=target["backend"])
    if dry_run:
        info(_("Plán: ověřit instalaci → porovnat dvě nejvyšší dochované generace.") if generations else
             _("Plán: ověřit instalaci → sestavit recept → stáhnout chybějící staré cesty → nvd diff."))
        info(_("Bez připojení a sestavení. Vzdálený systém se při porovnání nemění."))
        return 0

    if generations:
        snapshot = status(target)
        preflight(target, snapshot)
        command = connection(target, snapshot)
        # SSH bez TTY skryje místní terminál před nvd; barvy volíme podle místního výstupu.
        script = generation_script(name)
        if color_enabled(sys.stdout):
            script = "NIXBERRY_DIFF_COLOR=always\n" + script
        return subprocess.run(command + [script], check=False).returncode

    with tempfile.TemporaryDirectory(prefix="nixberry-diff-") as directory:
        frozen = freeze_recipe(recipe, name, target, directory)
        snapshot = status(target)
        previous = preflight(target, snapshot)
        info(_("Běžící systém: {system}"), system=previous)
        info(_("Sestavuji recept pro porovnání…"))
        expected = build(frozen, name, directory)
        current_snapshot = status(target)
        if preflight(target, current_snapshot) != previous:
            raise RuntimeError(_("Během sestavení se systém cíle změnil. Spusť diff znovu."))
        command = connection(target, current_snapshot)
        info(_("Stahuji chybějící části běžícího systému do místní Nix store…"))
        subprocess.run(["nix", "copy", "--from", "ssh://" + command[-1], previous], check=True,
                       env={**os.environ, "NIX_SSHOPTS": shlex.join(command[1:command.index("--")])})
        # Starý systém držíme po dobu nvd stejně jako nově sestavený systém.
        subprocess.run(["nix-store", "--add-root", str(Path(directory) / "previous"),
                        "--indirect", "--realise", previous], check=True, stdout=subprocess.DEVNULL)
        if preflight(target, status(target)) != previous:
            raise RuntimeError(_("Během přenosu se systém cíle změnil. Spusť diff znovu."))
        info(_("Nový recept: {system}"), system=expected)
        return subprocess.run(["nvd", "diff", previous, expected], check=False).returncode
