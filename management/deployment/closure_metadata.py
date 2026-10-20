"""Read closure metadata and expose read-only Nix queries to nvd."""

import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from messages import _, error


STORE_PATH = re.compile(r"/nix/store/[a-z0-9]{32}-[A-Za-z0-9+._?=-]+")


def read_snapshot(system, connection=None):
    # No imports, remote writes or remote helper installation. Keep subprocess
    # stderr intact; the NUL separates path sizes from selected-package paths.
    script = (
        "set -eu; nix --extra-experimental-features nix-command path-info --size --recursive "
        + shlex.quote(system) + "; printf '\\000'; nix-store --query --references "
        + shlex.quote(system + "/sw")
    )
    command = [*connection, script] if connection else ["sh", "-c", script]
    output = subprocess.check_output(command, text=True, timeout=120)
    try:
        encoded, selected = output.split("\0")
        data = {}
        for line in encoded.splitlines():
            path, size = line.split()
            if not STORE_PATH.fullmatch(path) or not size.isascii() or not size.isdecimal() or path in data:
                raise ValueError
            data[path] = int(size)
        if system not in data:
            raise ValueError
        selected = selected.splitlines()
        if not set(selected).issubset(data):
            raise ValueError
        return {"system": system, "closure": sorted(data), "selected": selected,
                "size": sum(data.values())}
    except (ValueError, TypeError, KeyError, AttributeError) as failure:
        raise RuntimeError(_("Invalid closure metadata received for comparison.")) from failure


def comparison_command(directory, previous, expected):
    # nvd's supported --nix-bin-dir interface reads these snapshots instead of
    # querying an imported closure. Only metadata is supplied, never executable
    # files from the managed target. The real nvd keeps its normal rendering.
    root = Path(directory) / "metadata"
    root.mkdir()
    snapshots = {}
    for label, snapshot in (("running", previous), ("recipe", expected)):
        path = root / label
        (path / "sw").mkdir(parents=True)
        snapshots[str(path)] = snapshot
    data = root / "snapshots.json"
    data.write_text(json.dumps(snapshots))
    binaries = root / "bin"
    binaries.mkdir()
    helper = str(Path(__file__).resolve())
    for executable in ("nix", "nix-store"):
        path = binaries / executable
        path.write_text(
            "#!" + sys.executable + "\nimport os, sys\n"
            + "os.execv(sys.executable, "
            + repr([sys.executable, helper, str(data), executable]) + " + sys.argv[1:])\n"
        )
        path.chmod(0o700)
    # nvd uses the real version only to decide whether to pass nix-command.
    (binaries / "nix-build").symlink_to(shutil.which("nix-build") or "nix-build")
    return ["nvd", "--nix-bin-dir", str(binaries), "diff", *snapshots]


def query(snapshots, executable, arguments):
    if arguments[:2] == ["--extra-experimental-features", "nix-command"]:
        arguments = arguments[2:]
    for root, snapshot in snapshots.items():
        if executable == "nix-store":
            if arguments == ["--query", "--requisites", root]:
                return "\n".join(snapshot["closure"]) + "\n"
            if arguments == ["--query", "--references", root + "/sw"]:
                return "\n".join(snapshot["selected"]) + "\n"
        if executable == "nix" and arguments == ["path-info", "--closure-size", root]:
            return snapshot["system"] + " " + str(snapshot["size"]) + "\n"
    raise ValueError(_("Unsupported nvd metadata query."))


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            snapshots = json.load(stream)
        sys.stdout.write(query(snapshots, sys.argv[2], sys.argv[3:]))
    except (OSError, ValueError, IndexError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
