"""Restore a preserved Restic source into a distinct declared, verified target."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import quote

from messages import _, forward_stderr
import restic_access
import restic_backup
import restore_access
from runtime import capabilities, status
from updater import connection, freeze_recipe, preflight, system_path


def projection(recipe, name):
    expression = ('systems: let c=systems.' + json.dumps(name).replace('${', r'\${') + '.config; in {'
                  ' restore=c.system.build.nixberryRestorePolicy or null; '
                  'capture=c.system.build.nixberryCapturePolicy or null; systemPath=c.system.build.toplevel.outPath; }')
    result = subprocess.run(['nix', 'eval', '--json', '--no-update-lock-file', '--option',
        'allow-import-from-derivation', 'false', 'path:' + quote(recipe, safe='/') + '#nixberrySystems',
        '--apply', expression], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    forward_stderr(result.stderr)
    if result.returncode:
        raise RuntimeError(_("Could not evaluate the prepared Restic restore target."))
    return json.loads(result.stdout)


def remote(command, script, timeout, *, contents=None):
    try:
        result = subprocess.run([command[0], '-T', '-o', 'ForwardAgent=no', *command[1:], script],
            input=contents, stdin=subprocess.DEVNULL if contents is None else None,
            capture_output=True, text=contents is None, timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        forward_stderr(failure.stderr)
        raise RuntimeError(_("Destination restoration timed out; retry the same request to recover its transaction.")) from None
    forward_stderr(result.stderr)
    if result.returncode:
        raise RuntimeError(_("Destination restoration failed; inspect its diagnostics and retained transaction."))
    return json.loads(result.stdout)


def run(recipe, target, name, source, access, snapshot, *, apply=False, dry_run=False):
    if sys.platform != 'linux':
        raise RuntimeError(_("Restic restoration controller operations currently require Linux."))
    if not source or source == name or not isinstance(snapshot, str) or not restic_backup.ID.fullmatch(snapshot):
        raise ValueError(_("Restic restore requires a distinct source name and a full Restic receipt ID."))
    if not capabilities(target)['system']:
        raise ValueError(_("Restic restoration requires a declared target with verified live system access."))
    context = restic_access.load(access, source)
    with tempfile.TemporaryDirectory(prefix='nixberry-restore-') as directory:
        frozen = freeze_recipe(recipe, name, target, directory)
        selected = projection(frozen, name)
        if not isinstance(selected['restore'], dict) or not isinstance(selected['capture'], dict):
            raise ValueError(_("Enable nixberry.backup.restore.prepare in the destination configuration first."))
        expected = system_path(selected['systemPath'])
        if dry_run or not apply:
            return {'action': 'restore', 'target': name, 'source': source, 'sourceId': context['sourceId'],
                    'snapshotId': snapshot, 'systemPath': expected, 'applied': False,
                    'readiness': 'not-checked', 'policy': selected['restore']}
        live = status(target)
        if preflight(target, live) != expected:
            raise RuntimeError(_("The running destination differs from this recipe; prepare it before restoration."))
        command = connection(target, live)
        prefix = 'set -eu; export PATH=/run/current-system/sw/bin:/run/wrappers/bin; '
        inspect = ("printf '{\"restore\":'; cat /etc/nixberry/restore.json; "
                   "printf ',\"capture\":'; cat /etc/nixberry/capture.json; "
                   "printf ',\"identity\":'; " + shlex.join([
                       '/run/current-system/sw/bin/nixberry-restore-target', 'status', '--system', expected, '--json'])
                   + "; printf '}\\n'")
        installed = remote(command, prefix + inspect, 30)
        identity = installed.get('identity') if isinstance(installed, dict) else None
        if (not isinstance(installed, dict) or not isinstance(identity, dict) or identity.get('systemPath') != expected
                or installed.get('restore') != selected['restore'] or installed.get('capture') != selected['capture']
                or not isinstance(identity.get('machineId'), str) or not restic_backup.snapshots.ID.fullmatch(identity['machineId'])):
            raise RuntimeError(_("The installed destination policies or identity differ from the selected restore configuration."))
        # Read secrets only after the target generation/policies/identity pass.
        contents = restore_access.pack(context)
        language = os.environ.get('NIXBERRY_LANG', 'en')
        language = language if language in ('en', 'cs') else 'en'
        arguments = ['/run/current-system/sw/bin/nixberry-restore-target', 'restore', '--access-stdin',
                     '--source', source, '--snapshot', snapshot, '--system', expected,
                     '--machine-id', identity['machineId'], '--apply', '--json']
        arguments = ['systemd-run', '--unit=nixberry-restore', '--collect', '--wait', '--pipe',
                     '--property=Type=exec', '--property=KillMode=control-group', '--property=TimeoutStopSec=10',
                     '--setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin', '--setenv=NIXBERRY_LANG=' + language,
                     '--', *arguments]
        report = remote(command, prefix + 'exec ' + shlex.join(arguments),
                        context['timeoutSeconds'] + 172800 + 600, contents=contents)
        if (not isinstance(report, dict) or report.get('applied') is not True or report.get('state') != 'recorded-restored'
                or report.get('machineId') != identity['machineId'] or report.get('systemPath') != expected
                or report.get('sourceId') != context['sourceId'] or report.get('snapshotId') != snapshot):
            raise RuntimeError(_("The destination did not confirm this exact Restic restoration."))
        return {'target': name, 'source': source, **report}
