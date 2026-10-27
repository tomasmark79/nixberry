"""Private controller run history and lightweight backup freshness monitoring."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import uuid

from messages import _, warning, forward_stderr

DEFAULTS = {'enable': False, 'maxAgeHours': 36, 'intervalMinutes': 15,
            'maxRunMinutes': 90, 'notify': True, 'repeatHours': 24}


def monitor_policy(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError(_("Invalid backup monitoring policy."))
    result = {**DEFAULTS, **value}
    if (any(type(result[key]) is not bool for key in ['enable', 'notify'])
            or any(type(result[key]) is not int or not 1 <= result[key] <= maximum
                   for key, maximum in [('maxAgeHours', 8760), ('intervalMinutes', 1440),
                                        ('maxRunMinutes', 10080), ('repeatHours', 8760)])):
        raise ValueError(_("Backup monitoring requires booleans and positive bounded age, interval, runtime and reminder values."))
    return result


def private(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError(_("Backup monitoring state has unsafe ownership, permissions or links."))


def state_directory(recipe, target, repository):
    root = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    if not root.is_absolute() or root.is_relative_to('/nix/store'):
        raise ValueError(_("Backup monitoring needs an absolute runtime XDG_STATE_HOME outside the Nix store."))
    namespace = json.dumps([str(Path(recipe).resolve()), target, str(Path(repository).resolve())])
    path = root / 'nixberry/backup-monitor' / hashlib.sha256(namespace.encode()).hexdigest()
    protected = [Path(recipe).resolve()]
    configurations = protected[0] / 'configurations'
    if configurations.is_dir():
        protected += [entry.resolve() for entry in configurations.iterdir() if entry.is_symlink() and entry.is_dir()]
    if path.resolve().is_relative_to('/nix/store') or any(path.resolve().is_relative_to(directory) for directory in protected):
        raise ValueError(_("Keep backup monitoring state outside the recipe and linked configurations."))
    return path


def load(directory, name):
    if not directory.exists() and not directory.is_symlink():
        return None
    private(directory, True)
    path = directory / name
    try:
        private(path)
    except FileNotFoundError:
        return None
    if path.stat().st_size > 65536:
        raise ValueError(_("The backup monitoring state is invalid."))
    with path.open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(_("The backup monitoring state is invalid."))
    return value


def save(directory, name, value):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(directory, True)
    try:
        private(directory / name)
    except FileNotFoundError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lock(directory, name):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(directory, True)
    descriptor = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private(directory / name)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def process_identity(pid):
    text = Path('/proc/' + str(pid) + '/stat').read_text()
    fields = text[text.rfind(')') + 2:].split()
    if fields[0] == 'Z':
        raise ProcessLookupError
    return {'pid': pid, 'startTicks': int(fields[19]),
            'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def valid_attempt(value, target):
    if value is None:
        return None
    integers = ['startedAt', 'pid', 'startTicks']
    allowed = {'schemaVersion', 'target', 'operationId', 'state', 'startedAt', 'finishedAt',
               'durationSeconds', 'pid', 'startTicks', 'bootId', 'lastSucceededAt', 'errorCode'}
    if (not isinstance(value, dict) or set(value) - allowed
            or value.get('schemaVersion') != 1 or value.get('target') != target
            or value.get('state') not in ['running', 'succeeded', 'failed']
            or not isinstance(value.get('operationId'), str) or not re.fullmatch('[0-9a-f]{32}', value['operationId'])
            or any(type(value.get(key)) is not int or value[key] <= 0 for key in integers)
            or not isinstance(value.get('bootId'), str)
            or ('lastSucceededAt' in value and (type(value['lastSucceededAt']) is not int or value['lastSucceededAt'] <= 0))
            or ('errorCode' in value and (not isinstance(value['errorCode'], str) or not re.fullmatch('[a-z0-9_]{1,80}', value['errorCode'])))
            or (value['state'] != 'running' and
                (type(value.get('finishedAt')) is not int or value['finishedAt'] < value['startedAt']
                 or type(value.get('durationSeconds')) is not int or value['durationSeconds'] < 0))):
        raise ValueError(_("The backup monitoring state is invalid."))
    return value


@contextmanager
def capture_run(directory, target):
    with lock(directory, 'capture.lock'):
        # Refuse damaged state rather than silently discarding earlier evidence.
        previous = valid_attempt(load(directory, 'last-run.json'), target)
        started = time.monotonic()
        attempt = {'schemaVersion': 1, 'target': target, 'operationId': uuid.uuid4().hex,
                   'state': 'running', 'startedAt': int(time.time()), **process_identity(os.getpid())}
        if previous and previous.get('lastSucceededAt') is not None:
            attempt['lastSucceededAt'] = previous['lastSucceededAt']
        save(directory, 'last-run.json', attempt)
        try:
            yield
        except BaseException as failure:
            code = getattr(failure, 'backup_code', 'capture_failed')
            if not isinstance(code, str) or not re.fullmatch('[a-z0-9_]{1,80}', code):
                code = 'capture_failed'
            attempt.update(state='failed', errorCode=code, finishedAt=max(int(time.time()), attempt['startedAt']),
                           durationSeconds=int(time.monotonic() - started))
            try:
                save(directory, 'last-run.json', attempt)
            except (OSError, ValueError):
                warning(_("Could not persist the failed backup attempt; inspect controller state."))
            raise
        else:
            finished = max(int(time.time()), attempt['startedAt'])
            attempt.update(state='succeeded', finishedAt=finished, lastSucceededAt=finished,
                           durationSeconds=int(time.monotonic() - started))
            save(directory, 'last-run.json', attempt)


def evaluate(target, policy, inventory, attempt=None, previous=None, error=None, now=None):
    now = int(time.time()) if now is None else now
    critical = []; warnings = []; latest = None; active = False
    external=False
    local_active=False
    if attempt and attempt.get('state')=='running':
        try:
            local_active=process_identity(attempt['pid'])=={key:attempt[key] for key in ['pid','startTicks','bootId']}
        except (OSError,ValueError,IndexError,KeyError):
            pass
    if isinstance(inventory,dict) and inventory.get('captureStatus') is not None:
        candidate=inventory['captureStatus']
        if (not isinstance(candidate,dict) or candidate.get('state') not in ['running','succeeded','failed']
                or type(candidate.get('startedAt')) is not int or candidate['startedAt']<=0
                or not isinstance(candidate.get('operationId'),str)
                or not re.fullmatch('[0-9a-f]{32}',candidate['operationId'])
                or candidate.get('origin','source') not in ['source','controller']
                or ('finishedAt' in candidate and (type(candidate['finishedAt']) is not int
                    or candidate['finishedAt']<candidate['startedAt']))
                or ('durationSeconds' in candidate and (type(candidate['durationSeconds']) is not int
                    or candidate['durationSeconds']<0))
                or (candidate['state']!='running' and not {'finishedAt','durationSeconds'} <= candidate.keys())):
            critical.append('invalid_inventory')
        elif (attempt is None
              or (candidate.get('origin','source')=='source' and candidate['startedAt']>=attempt['startedAt'])
              or (candidate.get('origin')=='controller' and not local_active
                  and candidate['startedAt']>attempt.get('finishedAt',attempt['startedAt']))):
            attempt={key:candidate[key] for key in ['state','startedAt','operationId','finishedAt','durationSeconds'] if key in candidate}
            attempt['location']='source' if candidate.get('origin','source')=='source' else 'nas-publication'
            external=True
    if attempt:
        if attempt['startedAt'] > now + 300 or attempt.get('finishedAt', 0) > now + 300:
            critical.append('clock_skew')
        if attempt['state'] == 'failed':
            critical.append('last_capture_failed')
        elif attempt['state'] == 'running':
            if external:active=True
            else:active=local_active
            if not active:
                critical.append('capture_interrupted')
            elif now - attempt['startedAt'] > policy['maxRunMinutes'] * 60:
                critical.append('capture_overdue')
    counts = {'verified': 0, 'captured': 0, 'invalid': 0, 'pending': 0}
    deferred = error == 'repository_busy' and active and 'capture_overdue' not in critical
    if inventory is not None:
        try:
            rows = inventory['snapshots']; pending = inventory.get('pendingCaptures', 0)
            if not isinstance(rows, list) or type(pending) is not int or pending < 0:
                raise ValueError
            valid = []
            for row in rows:
                if not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{32}', row.get('snapshotId', '')):
                    raise ValueError
                if row.get('state') in ['recorded-verified','recorded-captured']:
                    timestamp = row.get('createdAt')
                    if type(timestamp) is not int or timestamp <= 0:
                        raise ValueError
                    if timestamp > now + 300:
                        critical.append('clock_skew')
                    valid.append({'snapshotId': row['snapshotId'], 'createdAt': timestamp,
                                  'verification':'authenticated-readback' if row['state']=='recorded-verified' else 'producer-and-transport'})
                    counts['verified' if row['state']=='recorded-verified' else 'captured']+=1
                else:
                    counts['invalid'] += 1
            counts['pending'] = pending
            latest = max(valid, key=lambda row: row['createdAt']) if valid else None
            if counts['invalid']: warnings.append('invalid_snapshots')
            if pending: warnings.append('partial_captures')
        except (KeyError, ValueError, TypeError):
            critical.append('invalid_inventory')
    elif deferred:
        candidate = (previous or {}).get('latest')
        if (isinstance(candidate, dict) and type(candidate.get('createdAt')) is int and 0 < candidate['createdAt'] <= now + 300
                and isinstance(candidate.get('snapshotId'), str) and re.fullmatch('[0-9a-f]{32}', candidate['snapshotId'])):
            latest = {key: candidate[key] for key in ['snapshotId', 'createdAt']}
    else:
        critical.append('inventory_unavailable')
    if not latest and not deferred:
        critical.append('no_verified_backup')
    age = max(0, now - latest['createdAt']) if latest else None
    if age is not None and age > policy['maxAgeHours'] * 3600:
        critical.append('backup_stale')
    status = 'failed' if critical else 'warning' if warnings else 'running' if active else 'ok'
    return {'schemaVersion': 1, 'target': target, 'checkedAt': now, 'status': status,
            'alarm': bool(critical or warnings), 'issues': sorted(set(critical + warnings)),
            'latest': latest, 'ageSeconds': age, 'maxAgeSeconds': policy['maxAgeHours'] * 3600,
            'snapshots': counts, 'lastAttempt': attempt, 'inventoryDeferred': deferred,
            'freshCryptographicVerification': False, 'recorded': False}


def notify(report, settings, directory):
    result = {'enabled': settings['notify'], 'state': 'none'}
    if not settings['notify'] or report['status'] == 'running':
        return result
    previous = load(directory, 'notification.json')
    if previous and (type(previous.get('alarm')) is not bool or type(previous.get('sentAt')) is not int
                     or not isinstance(previous.get('fingerprint'), str)):
        raise ValueError(_("The backup monitoring state is invalid."))
    fingerprint = hashlib.sha256(json.dumps([report['issues'],
        (report.get('lastAttempt') or {}).get('operationId') if report['alarm'] else None]).encode()).hexdigest()
    if not report['alarm'] and not (previous and previous['alarm']):
        return result
    if (previous and previous['fingerprint'] == fingerprint
            and 0 <= report['checkedAt'] - previous['sentAt'] < settings['repeatHours'] * 3600):
        return {**result, 'state': 'suppressed'}
    title = _("NixBerry backup: {target}").format(target=report['target'])
    body = (_("Backup monitoring needs attention. Run: nixberry backup-check {target}")
            if report['alarm'] else _("Backup monitoring recovered; the latest recorded backup is recent.")).format(target=report['target'])
    try:
        response = subprocess.run(['notify-send', '--app-name=NixBerry',
            '--urgency=' + ('critical' if report['alarm'] else 'normal'), '--', title, body],
            capture_output=True, text=True, check=False, timeout=15)
        forward_stderr(response.stderr)
        if response.returncode:
            raise OSError
    except (OSError, subprocess.SubprocessError):
        warning(_("Backup notification delivery failed; inspect the protected health report."))
        return {**result, 'state': 'failed'}
    save(directory, 'notification.json', {'alarm': report['alarm'], 'sentAt': report['checkedAt'],
                                        'fingerprint': fingerprint})
    return {**result, 'state': 'sent'}


def check(recipe, target, settings, inventory_call, apply=False):
    directory = state_directory(recipe, target, settings['repository'])
    attempt = None; previous = None; invalid = False
    try:
        attempt = valid_attempt(load(directory, 'last-run.json'), target)
        previous = load(directory, 'latest.json')
        if previous and (previous.get('schemaVersion') != 1 or previous.get('target') != target):
            raise ValueError
    except (OSError, ValueError, TypeError):
        invalid = True
    inventory = None; error = None
    try:
        inventory = inventory_call()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as failure:
        error = getattr(failure, 'backup_code', 'inventory_unavailable')
    report = evaluate(target, settings['monitor'], inventory, attempt, previous, error)
    if invalid:
        report.update(status='failed', alarm=True, issues=sorted(set(report['issues'] + ['invalid_state'])))
    report['stateDirectory'] = str(directory)
    report['notification'] = {'enabled': settings['monitor']['notify'], 'state': 'not-requested'}
    if apply:
        try:
            with lock(directory, 'check.lock'):
                report['recorded'] = True
                # Preserve the health finding even if notification transport fails.
                save(directory, 'latest.json', report)
                if not invalid:
                    report['notification'] = notify(report, settings['monitor'], directory)
                save(directory, 'latest.json', report)
        except (OSError, ValueError, TypeError):
            report.update(recorded=False, status='failed', alarm=True,
                          issues=sorted(set(report['issues'] + ['state_write_failed'])))
            report['notification']['state'] = 'failed'
            warning(_("Could not persist backup monitoring state; inspect controller permissions."))
            try:
                save(directory, 'latest.json', report)
            except (OSError, ValueError):
                pass
    return report
