"""Run configuration-owned capture on a declared, verified installed source."""
import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from messages import _, forward_stderr
from runtime import status, capabilities
from updater import connection, freeze_recipe, preflight, system_path
import recovery_recipe

ACTIONS = ('snapshot', 'snapshots', 'snapshot-remove', 'snapshot-resume')


def projection(recipe, name):
    expression = ('systems: let c=systems.' + json.dumps(name).replace('${', r'\${')
        + '.config; b=c.nixberry.backup or {}; enabled=b.enable or false; in {'
        + ' inherit enabled; captureMethod=b.captureMethod or null; '
        + 'capture=c.system.build.nixberryCapturePolicy or null; '
        + 'restic=c.system.build.nixberryBackupPolicy or null; '
        + 'systemPath=if enabled then c.system.build.toplevel.outPath else null; }')
    # --no-write-lock-file permits hypothetical updates even alongside
    # --no-update-lock-file. Require an unchanged lock instead.
    result = subprocess.run(['nix', 'eval', '--json', '--no-update-lock-file',
                            '--option', 'allow-import-from-derivation', 'false',
                            'path:' + quote(recipe, safe='/') + '#nixberrySystems', '--apply', expression],
                            stdin=subprocess.DEVNULL, text=True, capture_output=True)
    forward_stderr(result.stderr)
    if result.returncode:
        raise RuntimeError(_("Could not evaluate the selected native backup configuration."))
    return json.loads(result.stdout)


def remote(command, script, timeout, *, input=None):
    try:
        result = subprocess.run([command[0], '-T', '-o', 'ForwardAgent=no', *command[1:], script],
                                input=input, stdin=subprocess.DEVNULL if input is None else None, text=input is None,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        forward_stderr(failure.stderr)
        raise RuntimeError(_("The source operation timed out; inspect writer recovery before retrying.")) from None
    forward_stderr(result.stderr)
    if result.returncode:
        raise RuntimeError(_("The source operation failed; inspect its diagnostics and retained capture state."))
    return json.loads(result.stdout)


def prepare_archive(frozen, name, target, settings, directory):
    hosts = {}
    if target['backend'] != 'rpi' and not target.get('offline'):
        selected = json.loads(subprocess.check_output(['nix', 'eval', '--json', '--no-write-lock-file',
            'path:' + quote(frozen, safe='/') + '#nixberryDeployment.hosts.' + json.dumps(target['host'])], text=True))
        hosts = {target['host']: selected}
    with recovery_recipe.selected_recipe(frozen, name, target, hosts) as selected:
        if projection(selected, name) != settings:
            raise RuntimeError(_("The selected recovery recipe does not reproduce the expected source generation and policy."))
        destination = Path(directory) / 'recovery.tar'
        metadata = {'format': recovery_recipe.FORMAT, 'sourceId': settings['restic']['sourceId'],
                    'target': name, 'systemPath': settings['systemPath'],
                    'policySha256': hashlib.sha256(recovery_recipe.encode(settings['restic']).encode()).hexdigest()}
        recovery_recipe.pack(selected, metadata, destination)
    contents = destination.read_bytes()
    return contents, recovery_recipe.validate(contents)


def run(recipe, target, action, name, *, snapshot=None, apply=False, dry_run=False):
    if action not in (*ACTIONS, 'backup', 'backup-stage'):
        raise ValueError(_("Unsupported native source backup operation."))
    if (action == 'snapshot-remove' and not re.fullmatch('[0-9a-f]{32}', snapshot or '')
            or action != 'snapshot-remove' and snapshot is not None):
        raise ValueError(_("snapshot-remove requires a full local capture ID from snapshots TARGET."))
    if action == 'snapshots' and apply:
        raise ValueError(_("This operation does not accept --apply."))
    if sys.platform != 'linux':
        raise RuntimeError(_("Native capture controller operations currently require Linux."))
    if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]*', name) or not capabilities(target)['system']:
        raise ValueError(_("Native capture requires a declared target with verified live system access."))
    with tempfile.TemporaryDirectory(prefix='nixberry-capture-') as directory:
        frozen = freeze_recipe(recipe, name, target, directory)
        settings = projection(frozen, name)
        if settings['enabled'] is not True or not isinstance(settings['capture'], dict):
            raise ValueError(_("Enable nixberry.backup in the selected NixOS configuration first."))
        if action in ('backup', 'backup-stage') and not isinstance(settings['restic'], dict):
            raise ValueError(_("Enable nixberry.backup.restic in the selected NixOS configuration first."))
        expected = system_path(settings['systemPath'])
        policy = settings['restic'] if action in ('backup', 'backup-stage') else settings['capture']
        archive, manifest = prepare_archive(frozen, name, target, settings, directory) if action == 'backup-stage' else (None, None)
        if dry_run or not apply and action != 'snapshots':
            return {'action': action, 'target': name, 'snapshotId': snapshot, 'systemPath': expected,
                    'captureMethod': settings['captureMethod'], 'policy': policy,
                    'applied': False, 'readiness': 'not-checked',
                    **({'archiveSha256': hashlib.sha256(archive).hexdigest(), 'archiveBytes': len(archive),
                        'archiveFiles': sorted(manifest['files'])} if archive is not None else {})}
        live = status(target)
        if preflight(target, live) != expected:
            raise RuntimeError(_("The running system differs from this recipe; update it before native capture."))
        command = connection(target, live)
        guard = ('set -eu; export PATH=/run/current-system/sw/bin:/run/wrappers/bin; '
                 + 'test "$(readlink -f /run/current-system)" = ' + shlex.quote(expected) + '; ')
        policy_file = '/etc/nixberry/' + ('backup.json' if action in ('backup', 'backup-stage') else 'capture.json')
        if remote(command, guard + 'cat ' + policy_file, 30) != policy:
            raise RuntimeError(_("The installed capture policy differs from the selected configuration."))
        language = os.environ.get('NIXBERRY_LANG', 'en')
        language = language if language in ('en', 'cs') else 'en'
        tool = '/run/current-system/sw/bin/nixberry-' + ('restic' if action in ('backup', 'backup-stage') else 'capture')
        arguments = [tool, 'resume' if action == 'snapshot-resume' else action, '--json']
        if apply:
            arguments.append('--apply')
        if snapshot is not None:
            arguments += ['--snapshot', snapshot]
        if archive is not None:
            arguments += ['--system', expected, '--receive-sha256', hashlib.sha256(archive).hexdigest()]
        timeout = settings['capture']['timeoutSeconds'] + (policy['timeoutSeconds'] if action == 'backup' else 0) + 240
        result = remote(command, guard + 'exec env NIXBERRY_LANG=' + language + ' ' + shlex.join(arguments), timeout, input=archive)
        if not isinstance(result, dict) or (apply and result.get('applied') is not True):
            raise RuntimeError(_("The source did not confirm the requested native capture operation."))
        return {'target': name, **result}
