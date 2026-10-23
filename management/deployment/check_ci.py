#!/usr/bin/env python3
"""Run isolated NixBerry CI checks without contacting hosts."""

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from messages import _, ArgumentParser, error, info, success


TOOLS = (
    "nix", "nix-instantiate", "nix-build", "nix-store", "git", "bash", "msgfmt",
    "jq", "xmllint", "nvd", "pwsh", "ruff", "shellcheck", "actionlint",
)
SUITES = (
    "management/deployment", "management/libvirt", "management/hyperv",
    "management/rpi", "management/proxmox/lab",
)
TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test-only\n"


def public_recipe(source, destination):
    """Copy tracked files and relative internal links without directory redirects."""
    source, destination = Path(source).resolve(), Path(destination)
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=source).decode().split("\0")
    files = [Path(name) for name in names if name]
    for relative in files:
        original, copied = source / relative, destination / relative
        redirected = any(source.joinpath(*relative.parts[:index]).is_symlink()
                         for index in range(1, len(relative.parts)))
        if redirected or (original.is_symlink() and (
                original.readlink().is_absolute() or not original.resolve().is_relative_to(source))):
            raise RuntimeError(_("Tracked path escapes the isolated recipe: {path}.").format(path=relative))
        copied.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            copied.symlink_to(original.readlink())
        else:
            shutil.copy2(original, copied)
    (destination / "admin.pub").write_text(TEST_KEY)
    return files


def run(command, recipe, environment, *, label=None, discard=False):
    if label:
        info(_("Running CI check: {check}."), check=label)
    subprocess.run(command, cwd=recipe, env=environment, check=True,
                   stdout=subprocess.DEVNULL if discard else None)


def syntax_checks(recipe, files, environment):
    info(_("Running CI check: {check}."), check="syntax")
    shell, powershell, workflows = [], [], []
    for relative in files:
        path = recipe / relative
        if path.suffix == ".py":
            ast.parse(path.read_bytes(), filename=str(relative))
        elif path.suffix == ".nix":
            run(["nix-instantiate", "--parse", str(path)], recipe, environment, discard=True)
        if path.suffix in (".sh", ".bash") or relative == Path("nixberry"):
            shell.append(str(path))
            run(["bash", "-n", str(path)], recipe, environment)
        if path.suffix == ".ps1":
            powershell.append("'" + str(path).replace("'", "''") + "'")
        if relative.parts[:2] == (".github", "workflows") and path.suffix in (".yml", ".yaml"):
            workflows.append(str(path))
    if powershell:
        script = (
            "$failed=$false; foreach($path in @(" + ",".join(powershell) + ")) {"
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)>$null; "
            "foreach($item in $errors){[Console]::Error.WriteLine($item.ToString()); $failed=$true}"
            "}; if($failed){exit 1}"
        )
        run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], recipe, environment)
    run(["ruff", "check", "--select", "F821,F822,F823", "--exclude", "campaign_host.py", "management"],
        recipe, environment, label="python-names")
    run(["shellcheck", "-s", "bash", "--severity=error", *shell], recipe, environment, label="shellcheck")
    if workflows:
        run(["actionlint", *workflows], recipe, environment, label="workflow")


def prepare_store(recipe, environment):
    run(["nix", "flake", "archive", "--json", "--no-write-lock-file", "path:" + str(recipe)],
        recipe, environment, label="locked-inputs", discard=True)
    expression = (
        "let f = builtins.getFlake " + json.dumps("path:" + str(recipe)) + "; "
        "p = f.inputs.nixos-raspberrypi.inputs.nixpkgs.legacyPackages.x86_64-linux; "
        "in [ p.libvirt p.openssh p.nix ]"
    )
    run(["nix", "build", "--impure", "--no-link", "--no-write-lock-file", "--expr", expression],
        recipe, environment, label="fixture-runtime-dependencies")


def main():
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument("--suite", choices=("regression", "inventory", "all"), default="all",
                        help=_("Select regression checks, inventory checks, or both."))
    arguments = parser.parse_args()
    missing = [tool for tool in TOOLS if not shutil.which(tool)]
    if missing:
        raise RuntimeError(_("Required CI tools are missing: {tools}.").format(tools=", ".join(missing)))
    source = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="nixberry-ci-") as directory:
        recipe = Path(directory) / "recipe"
        files = public_recipe(source, recipe)
        info(_("CI recipe copy: {path}."), path=recipe)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("NIXBERRY_")}
        environment.update({
            "NIXBERRY_RECIPE": str(recipe), "NIXBERRY_LANG": "en", "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1", "TERM": "dumb", "PYTHONNOUSERSITE": "1",
        })
        for variable in ("NIXON_CONFIG_DIR", "SSH_AUTH_SOCK", "NIX_SSHOPTS", "PYTHONPATH"):
            environment.pop(variable, None)
        python = sys.executable
        # Check committed generated content before compilation can rewrite it.
        run([python, "management/deployment/compile_locales.py", "--check"], recipe, environment,
            label="catalogue-freshness")
        run([python, "-m", "unittest", "discover", "-s", "management/deployment", "-p", "test_message_contract.py"],
            recipe, environment, label="message-contract")
        run([python, "management/deployment/compile_locales.py"], recipe, environment, label="catalogue-compile")
        if arguments.suite in ("regression", "all"):
            syntax_checks(recipe, files, environment)
        prepare_store(recipe, environment)
        if arguments.suite in ("regression", "all"):
            program = (
                "import sys, unittest; "
                "suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern='test_*.py'); "
                "result = unittest.TextTestRunner().run(suite); "
                "raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)"
            )
            for suite in SUITES:
                run([python, "-c", program, suite], recipe, environment, label=suite)
        if arguments.suite in ("inventory", "all"):
            run([python, "management/deployment/check_inventory.py"], recipe, environment, label="inventory")
    success(_("All selected CI checks passed."))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, SyntaxError, ValueError, subprocess.SubprocessError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
