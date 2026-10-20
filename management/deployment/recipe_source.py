"""Capture linked configuration repositories inside an ordinary recipe snapshot."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from messages import _, ArgumentParser, error

IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "result", "result-*", "*.log")


@contextmanager
def materialized_recipe(recipe):
    root = Path(recipe).resolve()
    configurations = root / "configurations"
    linked = []
    for path in configurations.iterdir() if configurations.is_dir() else []:
        if not path.is_symlink() or not re.fullmatch(r"[a-z][a-z0-9-]*", path.name):
            continue
        if not path.is_dir() or not (path / "default.nix").is_file() or (path / "default.nix").is_symlink():
            raise RuntimeError(_("Linked configuration is not a directory with a regular default.nix: {path}.").format(path=path))
        target = path.resolve(strict=True)
        for directory, dirs, files in os.walk(target, followlinks=False):
            ignored = set(IGNORE(directory, [*dirs, *files]))
            dirs[:] = [name for name in dirs if name not in ignored]
            for name in [*dirs, *files]:
                entry = Path(directory, name)
                if name not in ignored and entry.is_symlink():
                    raise RuntimeError(_("Linked configuration contains a symbolic link: {path}.").format(path=entry))
        linked.append((path, target))
    if not linked:
        yield str(root)
        return
    with tempfile.TemporaryDirectory(prefix="nixberry-source-") as directory:
        destination = Path(directory) / "recipe"
        shutil.copytree(root, destination, symlinks=True, ignore=IGNORE)
        for path, target in linked:
            copied = destination / "configurations" / path.name
            copied.unlink()
            shutil.copytree(target, copied, symlinks=True, ignore=IGNORE)
            if path.resolve(strict=True) != target:
                raise RuntimeError(_("Linked configuration changed during capture: {path}.").format(path=path))
        yield str(destination)


def main():
    parser = ArgumentParser(description=_("Run NixBerry with linked configurations captured in its recipe."))
    parser.add_argument("recipe")
    parser.add_argument("arguments", nargs="...")
    args = parser.parse_args()
    environment = os.environ.copy()
    environment["NIXBERRY_RECIPE"] = str(Path(args.recipe).resolve(strict=True))
    with materialized_recipe(args.recipe) as recipe:
        return subprocess.run(["nix", "run", "--no-write-lock-file", "path:" + recipe + "#nixberry",
                               "--", *args.arguments], env=environment).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
