"""Inspect or restore a prepared destination with configuration-owned writers."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import stat
import subprocess
import contextlib
import signal
import sys

from messages import _, ArgumentParser, error
from recovery_recipe import SYSTEM
import snapshots
import unit_names

AUTOMATIC = {'nixberry-backup.service', 'nixberry-backup.timer',
             'nixberry-backup-prune.service', 'nixberry-backup-prune.timer',
             'nixberry-backup-check.service', 'nixberry-backup-check.timer'}


def policy(value):
    required = {'version', 'architecture', 'stateDirectory', 'applications', 'automaticUnits'}
    if (not isinstance(value, dict) or set(value) != required or type(value['version']) is not int
            or value['version'] != 1 or value['architecture'] not in ('x86_64-linux', 'aarch64-linux')
            or not isinstance(value['applications'], dict) or not value['applications']
            or not isinstance(value['automaticUnits'], list)
            or any(not isinstance(unit, str) or unit not in AUTOMATIC for unit in value['automaticUnits'])):
        raise ValueError(_("Invalid destination restore policy."))
    snapshots.absolute(value['stateDirectory'])
    if not re.fullmatch('/[A-Za-z0-9_./-]+', value['stateDirectory']):
        raise ValueError(_("Restore state requires a normalized path without spaces or systemd specifiers."))
    state = Path(value['stateDirectory'])
    for name, app in value['applications'].items():
        if (not isinstance(name, str) or not snapshots.NAME.fullmatch(name) or not isinstance(app, dict)
                or set(app) != {'paths', 'units'} or not isinstance(app['paths'], list) or not app['paths']
                or not isinstance(app['units'], list) or any(not unit_names.valid(unit, capture=False) for unit in app['units'])):
            raise ValueError(_("Restore applications require paths and explicit non-management writer units."))
        for path in app['paths']:
            snapshots.absolute(path)
            if Path(path).is_relative_to(state) or state.is_relative_to(path):
                raise ValueError(_("Restore state must stay outside application data paths."))
    return value


def policy_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def marker_path(value, *, specifier=False):
    identity = '%m' if specifier else snapshots.machine_id()
    return Path(value['stateDirectory']) / ('completed-' + policy_hash(value) + '-' + identity + '.json')


def guard_text(value):
    return '[Unit]\nConditionPathExists=' + str(marker_path(value, specifier=True)) + '\n'


def completed(value):
    directory = snapshots.secure_directory(value['stateDirectory'])
    marker = marker_path(value)
    if not directory.exists() or not marker.exists() and not marker.is_symlink():
        return False
    record = snapshots.read_record(marker)
    fields = {'version', 'machineId', 'policySha256', 'sourceId', 'snapshotId', 'systemPath', 'completedAt'}
    if (not isinstance(record, dict) or set(record) != fields or type(record['version']) is not int or record['version'] != 1
            or record['machineId'] != snapshots.machine_id() or record['policySha256'] != policy_hash(value)
            or not isinstance(record['snapshotId'], str) or not re.fullmatch('[0-9a-f]{64}', record['snapshotId'])
            or not isinstance(record['sourceId'], str) or not re.fullmatch('[A-Za-z0-9:_.-]{1,128}', record['sourceId'])
            or not isinstance(record['systemPath'], str) or not SYSTEM.fullmatch(record['systemPath'])
            or type(record['completedAt']) is not int or record['completedAt'] < 0):
        raise ValueError(_("Restore completion does not match this destination and policy."))
    return True


def unit_state(value, unit):
    failure = _("Destination writer inspection failed or timed out; inspect the external tool diagnostics.")
    result = snapshots.run(['systemctl', 'show', '--property=LoadState,ActiveState,NeedDaemonReload,DropInPaths', unit],
                           timeout=15, failure_message=failure)
    fields = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
    if (fields.get('LoadState') != 'loaded' or fields.get('NeedDaemonReload') != 'no'
            or fields.get('ActiveState') not in ('active', 'inactive', 'failed')):
        raise RuntimeError(_("A restore writer is unavailable, changing state or awaiting daemon reload."))
    expected = guard_text(value)
    guarded = False
    for path in shlex.split(fields.get('DropInPaths', '')):
        try:
            with Path(path).open('rb') as stream:
                entry = os.fstat(stream.fileno())
                if (stat.S_ISREG(entry.st_mode) and entry.st_uid == snapshots.OWNER_UID and not entry.st_mode & 0o022
                        and stream.read(16385) == expected.encode()):
                    guarded = True
        except FileNotFoundError:
            continue
    if not guarded:
        raise RuntimeError(_("The loaded writer lacks the expected destination restore guard."))
    # Inspect the manager's effective conditions: later drop-ins may reset a
    # condition list even while our source file remains in DropInPaths.
    encoded = ''.join(char if char.isascii() and char.isalnum() else '_' + format(ord(char), '02x') for char in unit)
    conditions = json.loads(snapshots.run(['busctl', '--json=short', 'get-property', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1/unit/' + encoded, 'org.freedesktop.systemd1.Unit', 'Conditions'],
        timeout=15, failure_message=failure))
    if (not isinstance(conditions, dict) or conditions.get('type') != 'a(sbbsi)'
            or not isinstance(conditions.get('data'), list)
            or not any(isinstance(item, list) and len(item) == 5 and item[0] == 'ConditionPathExists'
                and item[1] is False and item[2] is False and item[3] == str(marker_path(value))
                for item in conditions['data'])):
        raise RuntimeError(_("The effective writer conditions do not contain the required restore guard."))
    return fields['ActiveState']


def path_state(value, *, allow_leaf_symlink=False):
    path = Path(value)
    symbolic = path.is_symlink()
    if symbolic and not allow_leaf_symlink or path.parent.resolve(strict=False) != path.parent:
        raise ValueError(_("Restore paths must not contain symbolic links."))
    if symbolic:
        return {'path': value, 'exists': True, 'empty': False}
    exists = path.exists()
    empty = not exists
    if exists and path.is_dir():
        with os.scandir(path) as entries:
            empty = next(entries, None) is None
    return {'path': value, 'exists': exists, 'empty': empty}


def status(value, *, expected_system=None, inspect_paths=True):
    value = policy(value)
    system = snapshots.running_system()
    if (not SYSTEM.fullmatch(system) or expected_system is not None and expected_system != system
            or platform.machine() + '-linux' != value['architecture']):
        raise RuntimeError(_("The destination generation or architecture differs from the expected restore target."))
    done = completed(value)
    pending_path = Path(value['stateDirectory']) / 'restore-pending.json'
    interrupted = pending_path.exists() or pending_path.is_symlink()
    units = sorted(set(value['automaticUnits']) | {unit for app in value['applications'].values() for unit in app['units']})
    states = {unit: unit_state(value, unit) for unit in units}
    paths = [path_state(path, allow_leaf_symlink=done)
             for path in sorted({path for app in value['applications'].values() for path in app['paths']})] if inspect_paths else []
    return {'systemPath': system, 'machineId': snapshots.machine_id(), 'policySha256': policy_hash(value),
            'architecture': value['architecture'], 'completed': done, 'pending': interrupted, 'units': states, 'paths': paths,
            'ready': not done and not interrupted and all(state in ('inactive', 'failed') for state in states.values()) and all(path['empty'] for path in paths)}


def require_managed():
    fields = dict(line.split('=', 1) for line in snapshots.run(['systemctl', 'show',
        '--property=MainPID,KillMode,ActiveState', 'nixberry-restore.service']).splitlines() if '=' in line)
    if (fields.get('MainPID') != str(os.getpid()) or fields.get('KillMode') != 'control-group'
            or fields.get('ActiveState') not in ('activating', 'active')):
        raise RuntimeError(_("Applied restoration requires the managed nixberry-restore system service."))


def main(argv=None):
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument('action', choices=['status', 'restore', 'abort'], nargs='?', default='status')
    parser.add_argument('--config', default='/etc/nixberry/restore.json', help=_("Configuration-owned destination restore policy file"))
    parser.add_argument('--system', help=_("Expected running NixOS system generation"))
    parser.add_argument('--json', action='store_true', help=_("Print destination readiness as technical JSON"))
    parser.add_argument('--machine-id', help=_("Explicit verified destination machine ID"))
    parser.add_argument('--source', help=_("Preserved native backup source name"))
    parser.add_argument('--snapshot', help=_("Full native Restic completion snapshot ID"))
    access = parser.add_mutually_exclusive_group()
    access.add_argument('--backup-access', help=_("Private native restore storage access context on this destination"))
    access.add_argument('--access-stdin', action='store_true', help=_("Receive bounded restore credentials through SSH standard input"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true', help=_("Restore application data and start configured destination writers"))
    modes.add_argument('--dry-run', action='store_true', help=_("Inspect restoration without publishing application data"))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RuntimeError(_("Destination restore inspection requires root."))
    with Path(args.config).open('rb') as stream:
        contents = stream.read(1024 * 1024 + 1)
    if len(contents) > 1024 * 1024:
        raise ValueError(_("Invalid destination restore policy."))
    value = json.loads(contents)
    if args.action == 'status':
        if args.apply or args.backup_access or args.access_stdin or args.source or args.snapshot or args.machine_id:
            parser.error(_("Restore access and mutation options only apply to destination restore."))
        result = status(value, expected_system=args.system)
    else:
        if args.apply and args.action == 'restore':
            require_managed()
        import native_access
        import native_restic
        import restore_access
        import restore_apply
        if args.action == 'restore' and (not all((args.system, args.machine_id, args.source, args.snapshot)) or not (args.backup_access or args.access_stdin)):
            parser.error(_("Destination restore requires system, machine ID, source, snapshot and private storage access."))
        def interrupted(signum, frame):
            raise InterruptedError(_("Restoration was interrupted; retry the same request to recover its private transaction."))
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, interrupted)
        with Path('/etc/nixberry/capture.json').open('rb') as stream:
            capture = json.loads(stream.read(1024 * 1024 + 1))
        if args.action == 'abort':
            if not args.system or not args.machine_id or args.source or args.snapshot or args.backup_access or args.access_stdin:
                parser.error(_("Abort requires the explicit destination system and machine ID without storage access options."))
            result = restore_apply.abort(value, capture, args.system, args.machine_id, apply=args.apply)
            print(json.dumps(result, sort_keys=True))
            return
        with contextlib.ExitStack() as stack:
            if args.access_stdin:
                context = stack.enter_context(restore_access.receive(restore_access.read(sys.stdin.buffer), args.source))
            else:
                context = native_access.load(args.backup_access, args.source)
                native_access.runtime(context)
            reader = native_restic.Restic(context, owner_uid=0, repository_id=context['repositoryId'], read_only=True)
            result = restore_apply.restore(value, capture, reader, args.snapshot, args.system, args.machine_id, apply=args.apply)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
        error('{error}', error=str(failure))
        raise SystemExit(1)
