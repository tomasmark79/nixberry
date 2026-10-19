"""Vyhodnocení instalačních nástrojů až při operaci, která je potřebuje."""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from messages import _, error
from updater import freeze_recipe


def launch(target, command, arguments):
    arguments = list(arguments)
    method = None
    if "--method" in arguments:
        index = arguments.index("--method")
        method = arguments[index + 1]
        if method not in ("iso", "image"):
            raise ValueError(_('Metoda nasazení musí být iso nebo image.'))
        del arguments[index:index + 2]
    recipe = os.environ.get("NIXBERRY_RECIPE")
    if not recipe or not (Path(recipe) / "flake.nix").is_file():
        raise RuntimeError(_('Operaci spusť přes spouštěč nixberry s úplným projektovým receptem.'))
    expected = json.loads(Path(os.environ["NIXBERRY_DISPATCH_TARGET"]).read_text())
    with tempfile.TemporaryDirectory(prefix="nixberry-tools-") as directory:
        frozen = freeze_recipe(str(Path(recipe).resolve()), target, expected, directory)
        return subprocess.run([
            "nix", "shell", "--no-write-lock-file",
            "path:" + frozen + "#" + ("nixberryToolsByMethod." + method + "." if method else "nixberryTools.") + target,
            "--command", command, *arguments,
        ], check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(launch(sys.argv[1], sys.argv[2], sys.argv[3:]))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
