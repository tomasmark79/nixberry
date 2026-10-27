"""Configuration-owned backup providers behind the ordinary NixBerry contract."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from messages import _, forward_stderr
from recipe_source import materialized_recipe
import backup_watch

ACTIONS = ('backup', 'backups', 'verify-backup', 'restore', 'recover-source', 'backup-prune', 'backup-schedule', 'backup-check', 'backup-monitor')
BACKENDS = ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware', 'rpi')


def policy(target):
    value = target.get('backup', {})
    allowed = {'enable', 'provider', 'repository', 'identityFile', 'recipient', 'restoreBackends', 'schedule', 'keepLast', 'monitor'}
    if not isinstance(value, dict) or set(value) - allowed or type(value.get('enable', False)) is not bool:
        raise ValueError(_("Invalid backup policy in the target declaration."))
    result = {'enable': False, 'provider': None, 'repository': None, 'identityFile': None,
              'recipient': None, 'restoreBackends': [], 'schedule': None, 'keepLast': None, **value}
    result['monitor'] = backup_watch.monitor_policy(value.get('monitor', {}))
    if not result['enable']:
        return result
    provider = result['provider']
    paths = [result['repository'], result['identityFile']]
    if (not isinstance(provider, str) or not re.fullmatch(r'configurations(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+\.py', provider)
            or any(not isinstance(path, str) or not path.startswith('/') or path.startswith('/nix/store/')
                   or any(ord(character) < 32 or ord(character) == 127 for character in path) for path in paths)
            or not isinstance(result['recipient'], str) or not re.fullmatch(r'age1[0-9a-z]{58}', result['recipient'])
            or not isinstance(result['restoreBackends'], list) or any(value not in BACKENDS for value in result['restoreBackends'])
            or (result['schedule'] is not None and (not isinstance(result['schedule'], str) or not result['schedule']
                or any(ord(character) < 32 or ord(character) == 127 for character in result['schedule'])))
            or (result['keepLast'] is not None and (type(result['keepLast']) is not int or result['keepLast'] < 2))):
        raise ValueError(_("Enabled backup requires a provider, private runtime paths, a public age recipient and valid scheduling/retention settings."))
    return result


def available(target):
    result = []
    if policy(target)['enable']:
        result += ['backups', 'verify-backup', 'recover-source', 'backup-prune', 'backup-schedule', 'backup-check', 'backup-monitor']
        if target['capabilities']['system']:
            result += ['backup']
    if target['capabilities']['system']:
        result += ['restore']
    return result


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def schedule(recipe, name, settings, output, dry_run, monitor=False):
    if not re.fullmatch('[a-z][a-z0-9-]{0,63}', name):
        raise ValueError(_("Backup schedule target names require lowercase letters, digits and hyphens, starting with a letter."))
    if monitor and (not settings['monitor']['enable'] or not output):
        raise ValueError(_("backup-monitor requires backup.monitor.enable and --output NEW_DIRECTORY."))
    if not monitor and (not settings['schedule'] or not output):
        raise ValueError(_("backup-schedule requires backup.schedule and --output NEW_DIRECTORY."))
    destination = Path(output).expanduser().absolute()
    root = Path(recipe).resolve()
    if destination.exists() or destination.is_symlink() or destination == root or root in destination.parents:
        raise ValueError(_("Choose a new backup schedule directory outside the recipe."))
    launcher = root / 'nixberry'
    if not launcher.is_file():
        raise ValueError(_("The selected recipe has no nixberry launcher."))
    target = name
    name = ('nixberry-backup-monitor-' if monitor else 'nixberry-backup-') + name
    report = {'target': target, 'destination': str(destination), 'mode': 'monitor' if monitor else 'capture',
              'schedule': settings['schedule'], 'keepLast': settings['keepLast'],
              'installed': False, 'enabled': False, 'filesWritten': False}
    if dry_run:
        return report
    # Validate the calendar before writing a reviewable user-unit bundle.
    if not monitor:
        result = subprocess.run(['systemd-analyze', 'calendar', settings['schedule']],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError(_("The backup schedule is not a valid systemd calendar."))
        forward_stderr(result.stderr)
    target = report['target']
    # ExecStart expands dollars even inside quotes; Environment does not.
    prefix = unit_quote(str(launcher).replace('$', '$$'))
    service = ('[Unit]\nDescription=NixBerry backup ' + ('monitoring' if monitor else 'capture') + '\n\n[Service]\nType=oneshot\nUMask=0077\n'
               + 'Environment=' + unit_quote('NIXBERRY_RECIPE=' + str(root)) + '\n'
               + 'PassEnvironment=SSH_AUTH_SOCK\n'
               + 'ExecStart=' + prefix + (' backup-check ' if monitor else ' backup ') + unit_quote(target) + ' --apply --json\n')
    if not monitor and settings['keepLast'] is not None:
        service += 'ExecStartPost=' + prefix + ' backup-prune ' + unit_quote(target) + ' --apply --json\n'
    if monitor:
        service += 'TimeoutStartSec=2min\nTimeoutStopSec=15s\n'
    timer = ('[Unit]\nDescription=NixBerry encrypted backup schedule\n\n[Timer]\nOnCalendar='
             + (settings['schedule'] or '').replace('%', '%%') + '\nPersistent=true\nUnit=' + name
             + '.service\n\n[Install]\nWantedBy=timers.target\n')
    if monitor:
        timer = ('[Unit]\nDescription=NixBerry backup monitoring schedule\n\n[Timer]\nOnActiveSec=2min\n'
                 'OnUnitActiveSec=' + str(settings['monitor']['intervalMinutes']) + 'min\nRandomizedDelaySec=30s\n'
                 'AccuracySec=30s\nUnit=' + name + '.service\n\n[Install]\nWantedBy=timers.target\n')
        report.update(schedule=None, intervalMinutes=settings['monitor']['intervalMinutes'], keepLast=None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the final name exclusively; partial bundles remain visibly incomplete.
    destination.mkdir(mode=0o700)
    for filename, contents in [(name + '.service', service), (name + '.timer', timer)]:
        descriptor = os.open(destination / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(contents)
    return {**report, 'filesWritten': True}


def run(recipe, registry, action, name, *, source=None, snapshot=None, output=None, apply=False, dry_run=False):
    if sys.platform != 'linux':
        raise RuntimeError(_("Backup controller operations currently require Linux."))
    if not recipe or not (Path(recipe) / 'flake.nix').is_file():
        raise ValueError(_("Run backup operations through a complete NixBerry recipe."))
    owner = source if action == 'restore' else name
    if owner not in registry:
        raise ValueError(_("restore requires --from with a declared backup source."))
    settings = policy(registry[owner])
    if not settings['enable']:
        raise ValueError(_("Enable backup in the source target declaration first."))
    root = Path(recipe).resolve()
    protected = [root]
    configurations = root / 'configurations'
    if configurations.is_dir():
        protected += [path.resolve() for path in configurations.iterdir() if path.is_symlink() and path.is_dir()]
    if any(Path(settings[field]).resolve().is_relative_to(directory)
           for field in ['repository', 'identityFile'] for directory in protected):
        raise ValueError(_("Keep backup storage and identities outside the recipe and linked configurations."))
    if action in ('verify-backup', 'restore', 'recover-source') and not re.fullmatch('[0-9a-f]{32}', snapshot or ''):
        raise ValueError(_("Select a snapshot ID from backups TARGET using --snapshot ID."))
    if action == 'restore' and (owner == name or registry[name]['backend'] not in settings['restoreBackends']):
        raise ValueError(_("The backup provider does not support this distinct restore target backend."))
    if action in ('backup-schedule', 'backup-monitor'):
        return schedule(recipe, name, settings, output, dry_run, monitor=action == 'backup-monitor')
    if dry_run:
        return {'action': action, 'target': name, 'source': owner, 'snapshotId': snapshot,
                'policy': settings, 'output': output, 'applied': False, 'readiness': 'not-checked'}
    if action == 'backup-check':
        return backup_watch.check(recipe, name, settings,
                                  lambda: run(recipe, registry, 'backups', name), apply=apply)
    request = {'protocol': 1, 'action': action, 'target': name, 'source': owner,
               'snapshotId': snapshot, 'output': output, 'apply': apply,
               'recipe': str(Path(recipe).resolve()), 'policy': settings,
               'targetDeclaration': registry[name],
               'guestPython': os.environ.get('NIXBERRY_BACKUP_GUEST_PYTHON')}
    if action == 'backup' and apply:
        directory = backup_watch.state_directory(recipe, name, settings['repository'])
        with backup_watch.capture_run(directory, name):
            return invoke(request)
    return invoke(request)


def invoke(request):
    recipe = request['recipe']; settings = request['policy']
    with materialized_recipe(recipe) as frozen:
        program = (Path(frozen) / settings['provider']).resolve()
        if not program.is_relative_to(Path(frozen).resolve()) or not program.is_file():
            raise ValueError(_("The backup provider must be a regular file inside the captured configuration."))
        response = subprocess.run([sys.executable, str(program)], input=json.dumps(request), text=True,
                                  capture_output=True, check=False,
                                  timeout=60 if request['action'] == 'backups' else None,
                                  env={**os.environ, 'NIXBERRY_RECIPE': request['recipe']})
    forward_stderr(response.stderr)
    try:
        payload = json.loads(response.stdout)
        if not isinstance(payload, dict) or payload.get('protocol') != 1:
            raise ValueError
        if response.returncode or payload.get('ok') is not True:
            code = payload.get('error', 'provider_failed')
            if not isinstance(code, str) or not re.fullmatch('[a-z0-9_]{1,80}', code):
                code = 'provider_failed'
            failure = RuntimeError(_("Backup provider failed: {code}. Inspect retained private state before retrying.").format(code=code))
            failure.backup_code = code
            raise failure
        if not isinstance(payload.get('result'), dict):
            raise ValueError
        return payload['result']
    except (ValueError, TypeError) as failure:
        raise RuntimeError(_("The backup provider returned an invalid protocol response.")) from failure
