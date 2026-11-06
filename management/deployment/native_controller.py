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

from messages import _, forward_stderr
from runtime import status, capabilities
from updater import connection, freeze_recipe, preflight, system_path
import recovery_recipe
from recipe_source import recipe_uri

ACTIONS = ('snapshot', 'snapshots', 'snapshot-remove', 'snapshot-resume')
RESTIC_ACTIONS = ('backup', 'backup-stage', 'backup-prune', 'backup-check')
MAINTENANCE_ACTIONS = ('backup-prune', 'backup-check')


def projection(recipe, name):
    expression = ('systems: let c=systems.' + json.dumps(name).replace('${', r'\${')
        + '.config; b=c.nixberry.backup or {}; enabled=b.enable or false; in {'
        + ' inherit enabled; captureMethod=b.captureMethod or null; '
        + 'capture=c.system.build.nixberryCapturePolicy or null; '
        + 'restic=c.system.build.nixberryBackupPolicy or null; '
        + 'maintenance=c.system.build.nixberryBackupMaintenancePolicy or null; '
        + 'systemPath=if enabled then c.system.build.toplevel.outPath else null; }')
    # --no-write-lock-file permits hypothetical updates even alongside
    # --no-update-lock-file. Require an unchanged lock instead.
    result = subprocess.run(['nix', 'eval', '--json', '--no-update-lock-file',
                            '--option', 'allow-import-from-derivation', 'false',
                            recipe_uri(recipe) + '#nixberrySystems', '--apply', expression],
                            stdin=subprocess.DEVNULL, text=True, capture_output=True)
    forward_stderr(result.stderr)
    if result.returncode:
        raise RuntimeError(_("Could not evaluate the selected native backup configuration."))
    return json.loads(result.stdout)


def configuration_sources(frozen, name):
    """Select configuration trees from the evaluated target's module graph."""
    def evaluate(arguments):
        result = subprocess.run(['nix', *arguments], stdin=subprocess.DEVNULL, text=True, capture_output=True)
        forward_stderr(result.stderr)
        if result.returncode:
            raise RuntimeError(_("Could not evaluate the selected native backup configuration."))
        return json.loads(result.stdout)

    metadata = evaluate(['flake', 'metadata', '--json', '--no-update-lock-file', recipe_uri(frozen)])
    directory = Path(metadata['path']) / 'configurations'
    prefix = json.dumps(str(directory) + '/').replace('${', r'\${')
    target = json.dumps(name).replace('${', r'\${')
    expression = ('systems: let flatten = nodes: builtins.concatLists (map '
        + '(node: [ node.file ] ++ flatten node.imports) nodes); prefix = ' + prefix + '; '
        + 'in builtins.filter (file: builtins.substring 0 (builtins.stringLength prefix) file == prefix) '
        + '(flatten (systems.' + target + '.graph or []))')
    files = evaluate(['eval', '--json', '--no-update-lock-file', '--option', 'allow-import-from-derivation', 'false',
                      recipe_uri(metadata['path']) + '#nixberrySystems', '--apply', expression])
    return sorted({Path(file).relative_to(directory).parts[0] for file in files})


def remote(command, script, timeout, *, input=None, health=False):
    try:
        result = subprocess.run([command[0], '-T', '-o', 'ForwardAgent=no', *command[1:], script],
                                input=input, stdin=subprocess.DEVNULL if input is None else None, text=input is None,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        forward_stderr(failure.stderr)
        raise RuntimeError(_("The source operation timed out; inspect writer recovery before retrying.")) from None
    forward_stderr(result.stderr)
    if result.returncode not in ((0, 1) if health else (0,)):
        raise RuntimeError(_("The source operation failed; inspect its diagnostics and retained capture state."))
    try:
        report = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError(_("The source did not return valid native operation metadata.")) from None
    if health and (not isinstance(report, dict) or report.get('status') not in ('ok', 'stale', 'missing', 'future')
                   or result.returncode != int(report['status'] != 'ok')):
        raise RuntimeError(_("The source freshness status does not match its reported outcome."))
    return report


def prepare_archive(frozen, name, target, settings, directory):
    hosts = {}
    if target['backend'] != 'rpi' and not target.get('offline'):
        selected = json.loads(subprocess.check_output(['nix', 'eval', '--json', '--no-write-lock-file',
            recipe_uri(frozen) + '#nixberryDeployment.hosts.' + json.dumps(target['host'])], text=True))
        hosts = {target['host']: selected}
    sources = configuration_sources(frozen, name)
    with recovery_recipe.selected_recipe(frozen, name, target, hosts, sources=sources) as selected:
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
    if action not in (*ACTIONS, *RESTIC_ACTIONS):
        raise ValueError(_("Unsupported native source backup operation."))
    if (action == 'snapshot-remove' and not re.fullmatch('[0-9a-f]{32}', snapshot or '')
            or action != 'snapshot-remove' and snapshot is not None):
        raise ValueError(_("snapshot-remove requires a full local capture ID from snapshots TARGET."))
    if action in ('snapshots', 'backup-check') and apply:
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
        if action in RESTIC_ACTIONS and not isinstance(settings['restic'], dict):
            raise ValueError(_("Enable nixberry.backup.restic in the selected NixOS configuration first."))
        expected = system_path(settings['systemPath'])
        policy = settings['restic'] if action in RESTIC_ACTIONS else settings['capture']
        archive, manifest = prepare_archive(frozen, name, target, settings, directory) if action == 'backup-stage' else (None, None)
        if dry_run or not apply and action not in ('snapshots', *MAINTENANCE_ACTIONS):
            return {'action': action, 'target': name, 'snapshotId': snapshot, 'systemPath': expected,
                    'captureMethod': settings['captureMethod'], 'policy': policy,
                    **({'maintenance': settings.get('maintenance')} if action in MAINTENANCE_ACTIONS else {}),
                    'applied': False, 'readiness': 'not-checked',
                    **({'archiveSha256': hashlib.sha256(archive).hexdigest(), 'archiveBytes': len(archive),
                        'archiveFiles': sorted(manifest['files'])} if archive is not None else {})}
        return execute(target, name, action, settings, snapshot=snapshot, apply=apply, archive=archive)


def execute(target, name, action, settings, *, snapshot=None, apply=False, archive=None):
    """Use already evaluated policy/archive, including an update's frozen recipe."""
    expected = system_path(settings['systemPath'])
    policy = settings['restic'] if action in RESTIC_ACTIONS else settings['capture']
    live = status(target)
    if preflight(target, live) != expected:
        raise RuntimeError(_("The running system differs from this recipe; update it before native capture."))
    command = connection(target, live)
    guard = ('set -eu; export PATH=/run/current-system/sw/bin:/run/wrappers/bin; '
             + 'test "$(readlink -f /run/current-system)" = ' + shlex.quote(expected) + '; ')
    policy_file = '/etc/nixberry/' + ('backup.json' if action in RESTIC_ACTIONS else 'capture.json')
    if remote(command, guard + 'cat ' + policy_file, 30) != policy:
        raise RuntimeError(_("The installed capture policy differs from the selected configuration."))
    if action in MAINTENANCE_ACTIONS and (not isinstance(settings.get('maintenance'), dict)
            or remote(command, guard + 'cat /etc/nixberry/maintenance.json', 30) != settings['maintenance']):
        raise RuntimeError(_("The installed maintenance policy differs from the selected configuration."))
    language = os.environ.get('NIXBERRY_LANG', 'en')
    language = language if language in ('en', 'cs') else 'en'
    tool = '/run/current-system/sw/bin/nixberry-' + ('restic' if action in RESTIC_ACTIONS else 'capture')
    arguments = [tool, 'resume' if action == 'snapshot-resume' else action, '--json']
    if apply:
        arguments.append('--apply')
    if snapshot is not None:
        arguments += ['--snapshot', snapshot]
    if action == 'snapshot':
        arguments += ['--system', expected]
    if action == 'backup-prune':
        arguments += ['--system', expected]
    if archive is not None:
        arguments += ['--system', expected, '--receive-sha256', hashlib.sha256(archive).hexdigest()]
    timeout = settings['capture']['timeoutSeconds'] + (policy['timeoutSeconds'] if action in RESTIC_ACTIONS else 0) + 240
    result = remote(command, guard + 'exec env NIXBERRY_LANG=' + language + ' ' + shlex.join(arguments), timeout,
                    input=archive, health=action == 'backup-check')
    if not isinstance(result, dict) or (apply and result.get('applied') is not True):
        raise RuntimeError(_("The source did not confirm the requested native capture operation."))
    return {'target': name, **result}
