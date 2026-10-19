"""Update an installed system from the project recipe over verified SSH."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from runtime import installation_checks, read, ssh_command, status, verify, guest_host_option, direct_connection
from messages import _, info, success, warning


def system_path(value):
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-nixos-system-[A-Za-z0-9+._-]+", value):
        raise RuntimeError(_("The target or build did not return a valid system generation path."))
    return value


def connection(target, snapshot):
    command = ssh_command(target, snapshot)
    return [command[0], "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", *command[1:]]


def preflight(target, snapshot):
    # status verifies identity; also reject installation ISO and recovery SD here.
    checks = installation_checks(target)
    architecture = "aarch64" if target["backend"] == "rpi" else "x86_64"
    checks.append('test "$(uname -m)" = ' + shlex.quote(architecture))
    guarded = [check + " || { printf '%s\\n' " + shlex.quote(_("Installation check failed: {check}").format(check=check))
               + "; exit 1; }" for check in checks]
    output = read(connection(target, snapshot) + [
        "set -eu; " + "; ".join(guarded + ["readlink -f /run/current-system"])])
    return system_path(output)


def build(recipe, name, directory):
    attribute = "path:" + recipe + "#nixberrySystems." + name + ".config.system.build.toplevel"
    link = Path(directory) / "system"
    # A temporary GC root protects the result through transfer and activation; builds have no SSH timeout.
    subprocess.run(["nix", "build", "--no-write-lock-file", "--out-link", str(link), attribute],
                   check=True)
    return system_path(str(link.resolve(strict=True)))


def freeze_recipe(recipe, name, target, directory):
    metadata = json.loads(subprocess.check_output([
        "nix", "flake", "metadata", "--json", "--no-write-lock-file", "path:" + recipe], text=True))
    frozen = metadata["path"]
    # Protect both the snapshot and build from GC until the operation completes.
    subprocess.run(["nix-store", "--add-root", str(Path(directory) / "recipe"),
                    "--indirect", "--realise", frozen], check=True, stdout=subprocess.DEVNULL)
    declared = json.loads(subprocess.check_output([
        "nix", "eval", "--json", "--no-write-lock-file",
        "path:" + frozen + "#nixberryDeployment.targets." + name], text=True))
    # Commands come from the CLI package; a matching declaration guarantees validity.
    identity = {key: value for key, value in target.items()
                if key not in ("commands", "operations", "capabilities", "_guestConnection")}
    declared_identity = {key: value for key, value in declared.items() if key != "capabilities"}
    if declared_identity != identity:
        raise RuntimeError(
            _("The target declaration differs from the running CLI. Run the command again through the current nixberry launcher."))
    return frozen


def reboot_required(command):
    script = (
        "set -eu; test -d /run/booted-system; "
        'if [ "$(readlink -f /run/booted-system/kernel)" != '
        '"$(readlink -f /run/current-system/kernel)" ] || '
        '[ "$(readlink -f /run/booted-system/initrd)" != '
        '"$(readlink -f /run/current-system/initrd)" ] || '
        '! cmp -s /run/booted-system/kernel-params /run/current-system/kernel-params; '
        "then printf 'yes\\n'; else printf 'no\\n'; fi"
    )
    value = read(command + [script])
    if value not in ("yes", "no"):
        raise RuntimeError(_("Could not determine whether a reboot is needed."))
    return value == "yes"


def update(recipe, name, target, *, apply=False):
    if target["backend"] not in ("rpi", "libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
        raise RuntimeError(_("Updates are not implemented for this platform."))
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise RuntimeError(_("Invalid target name for update."))
    if not recipe or not Path(recipe, "flake.nix").is_file() or not Path(recipe, "flake.lock").is_file():
        raise RuntimeError(_("Run update through the nixberry launcher with a complete pinned recipe."))
    info(_("System update: {name} ({backend})"), name=name, backend=target["backend"])
    info(_("Configuration: {configuration}"),
         configuration=f"path:{recipe}#nixberrySystems.{name}.config.system.build.toplevel")
    host_option = guest_host_option(target)
    if not apply:
        info(_("Plan: verify the running installation → build → transfer over SSH → activate → verify."))
        direct = direct_connection(target)
        if direct:
            info(_("Direct SSH to VM: {endpoint}; verifying the SSH key and declared hostname."),
                 endpoint=f'{direct["user"]}@{direct["host"]}:{direct["port"]}')
        else:
            info(_("The address is resolved from the declaration and live state; Hyper-V uses Windows as a jump host."))
        info(_("Without --apply, no target connection, build or system change is performed."))
        info(_("Execute: {command}"), command=f"nixberry update {name}{host_option} --apply")
        return None

    with tempfile.TemporaryDirectory(prefix="nixberry-update-") as directory:
        frozen = freeze_recipe(recipe, name, target, directory)
        snapshot = status(target)
        previous = preflight(target, snapshot)
        info(_("Previous system: {system}"), system=previous)
        info(_("Building the target configuration…"))
        system = build(frozen, name, directory)
        # A long build must not activate on a stale address or a foreign VM.
        current_snapshot = status(target)
        current = preflight(target, current_snapshot)
        if current != previous:
            raise RuntimeError(_("The target system changed during the build. Run update again."))
        command = connection(target, current_snapshot)
        destination = "ssh://" + command[-1]
        ssh_options = shlex.join(command[1:command.index("--")])
        info(_("Transferring the system to {host}…"), host=command[-1])
        subprocess.run(["nix", "copy", "--to", destination, system], check=True,
                       env={**os.environ, "NIX_SSHOPTS": ssh_options})
        final_snapshot = status(target)
        if (final_snapshot["addresses"] != current_snapshot["addresses"]
                or preflight(target, final_snapshot) != previous):
            raise RuntimeError(_("The target changed during transfer. Activation was not performed; run update again."))
        activation = (
            'set -eu; test "$(readlink -f /run/current-system)" = ' + shlex.quote(previous)
            + "; nix-env -p /nix/var/nix/profiles/system --set " + shlex.quote(system)
            + "; " + shlex.quote(system + "/bin/switch-to-configuration") + " switch"
        )
        # A separate systemd unit lets activation finish even if sshd restarts.
        # It does not inherit SSH's PATH; nix-env requires an explicit environment.
        remote = (
            "systemd-run --unit=nixberry-update --collect --wait "
            "--setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin "
            "--property=Type=oneshot -- /bin/sh -c " + shlex.quote(activation)
        )
        info(_("Activating the new generation without rebooting…"))
        activated = subprocess.run(command + [remote], check=False)
        if activated.returncode:
            raise RuntimeError(
                _("Could not confirm activation (SSH or systemd-run). Check {logs} and {verify}. The generation may already have been set; no reboot was performed.").format(
                      logs=f"nixberry ssh {name}{host_option} -- journalctl -u nixberry-update",
                      verify=f"nixberry verify {name}{host_option}"))
        try:
            result = verify(target, system)
            needs_reboot = reboot_required(connection(target, {
                "state": "running", "addresses": result["addresses"]}))
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            raise RuntimeError(
                _("Activation completed, but the subsequent check failed: {error}. Once SSH is restored, run {command}.").format(
                      error=error, command=f"nixberry verify {name}{host_option}")) from error
    success(_("Verified: {name}, {hostname}, system on {root}."),
            name=name, hostname=result["hostname"], root=result["root"])
    print(system)
    if needs_reboot:
        warning(_("The boot configuration differs from the running system; a reboot is required."))
        info(_("When convenient: {command}"), command=f"nixberry ssh {name}{host_option} -- reboot")
        info(_("After boot: {command}"), command=f"nixberry verify {name}{host_option}")
    else:
        info(_("The kernel, initrd and boot parameters match the running system; no reboot is needed."))
    return {**result, "previousSystem": previous, "rebootRequired": needs_reboot}
