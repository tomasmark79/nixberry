"""Activate a system generation while excluding configuration-owned captures."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys

from messages import _, ArgumentParser, error
from recovery_recipe import SYSTEM
import snapshots


def system_path(value):
    if not isinstance(value, str) or not SYSTEM.fullmatch(value):
        raise ValueError(_("Update activation requires explicit NixOS system generation paths."))
    return value


def running_system():
    return system_path(str(Path('/run/current-system').resolve(strict=True)))


def load_policy(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    with path.open('rb') as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError(_("Invalid snapshot capture policy."))
    return snapshots.policy(json.loads(data))


def policies(system):
    # The installed policy owns existing recovery state. The replacement policy
    # may move that state or enable/disable capture during activation.
    return [value for path in ('/etc/nixberry/capture.json', system + '/etc/nixberry/capture.json')
            if (value := load_policy(path)) is not None]


def activate(previous, system, *, apply=False):
    system_path(previous)
    system_path(system)
    if running_system() != previous:
        raise RuntimeError(_("The running system changed before update activation."))
    selected = policies(system)
    result = {'previousSystem': previous, 'systemPath': system, 'applied': False}
    if not apply:
        return result
    with contextlib.ExitStack() as stack:
        states = {policy['stateDirectory']: policy for policy in selected}
        locked = {directory: stack.enter_context(snapshots.lock(states[directory]))
                  for directory in sorted(states)}
        for policy in selected:
            if snapshots.pending(policy, locked[policy['stateDirectory']]):
                raise RuntimeError(_("Interrupted capture needs writer recovery before update activation."))
        if running_system() != previous or policies(system) != selected:
            raise RuntimeError(_("The system or capture policies changed before update activation."))
        subprocess.run(['nix-env', '-p', '/nix/var/nix/profiles/system', '--set', system], check=True, stdout=sys.stderr)
        subprocess.run([system + '/bin/switch-to-configuration', 'switch'], check=True, stdout=sys.stderr)
    return {**result, 'applied': True}


def main(argv=None):
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument('--previous-system', required=True, help=_("Expected running NixOS system generation"))
    parser.add_argument('--system', required=True, help=_("Prepared replacement NixOS system generation"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true', help=_("Activate while holding both capture policy locks"))
    modes.add_argument('--dry-run', action='store_true', help=_("Review activation without changing profiles or services"))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RuntimeError(_("System activation requires root on the declared target."))
    print(json.dumps(activate(args.previous_system, args.system, apply=args.apply), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
        error("{error}", error=str(failure))
        raise SystemExit(1)
