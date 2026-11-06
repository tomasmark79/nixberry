"""Capture configured application data in read-only Btrfs snapshots."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid

from messages import _, ArgumentParser, error, forward_stderr

OWNER_UID = 0
NAME = re.compile(r'[a-z][a-z0-9-]{0,63}')
ID = re.compile(r'[0-9a-f]{32}')
UUID = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
PROTECTED = {'sshd.service', 'ssh.service', 'systemd-networkd.service',
             'systemd-resolved.service', 'NetworkManager.service', 'dnsmasq.service',
             'adguardhome.service', 'systemd-logind.service', 'systemd-journald.service'}


def absolute(value, *, root=False):
    if (not isinstance(value, str) or not value.startswith('/') or value.startswith('//')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or str(Path(value)) != value or '..' in Path(value).parts
            or (value == '/' and not root)):
        raise ValueError(_("Capture paths must be normalized absolute paths without control characters."))
    return value


def unit_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.(service|socket|timer)', value)
            or value.rsplit('.', 1)[0] in {unit.rsplit('.', 1)[0] for unit in PROTECTED}
            or value.startswith(('wireguard-', 'wg-quick@', 'nixberry-'))):
        raise ValueError(_("Capture writers and activation units must be services, sockets or timers outside networking and management."))
    return value


def policy(value):
    required = {'version', 'applications', 'roots', 'stateDirectory', 'minFreeBytes', 'timeoutSeconds'}
    if (not isinstance(value, dict) or set(value) != required
            or type(value['version']) is not int or value['version'] != 1):
        raise ValueError(_("Invalid snapshot capture policy."))
    absolute(value['stateDirectory'])
    if (type(value['minFreeBytes']) is not int or value['minFreeBytes'] < 1
            or type(value['timeoutSeconds']) is not int or not 1 <= value['timeoutSeconds'] <= 3600
            or not isinstance(value['applications'], dict) or not value['applications']
            or not isinstance(value['roots'], dict) or not value['roots']):
        raise ValueError(_("Invalid snapshot capture policy."))
    for name, app in value['applications'].items():
        if (not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(app, dict) or set(app) != {'paths', 'stopUnits'}
                or not isinstance(app['paths'], list) or not app['paths']
                or not isinstance(app['stopUnits'], list)):
            raise ValueError(_("Each captured application requires data paths and explicit writer units."))
        for path in app['paths']:
            absolute(path)
        for unit in app['stopUnits']:
            unit_name(unit)
    for name, root in value['roots'].items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(root, dict) or set(root) != {'path', 'snapshotDirectory'}:
            raise ValueError(_("Each snapshot root requires a source subvolume and a snapshot directory."))
        absolute(root['path'], root=True)
        absolute(root['snapshotDirectory'])
    roots = [root['path'] for root in value['roots'].values()]
    if len(set(roots)) != len(roots):
        raise ValueError(_("Snapshot roots must name distinct source subvolumes."))
    paths = [path for app in value['applications'].values() for path in app['paths']]
    internal = [value['stateDirectory'], *(root['snapshotDirectory'] for root in value['roots'].values())]
    if any(Path(a).is_relative_to(b) or Path(b).is_relative_to(a) for a in paths for b in internal):
        raise ValueError(_("Capture state and snapshot storage must stay outside application data paths."))
    return value


def run(command, *, timeout=120):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, 'LC_ALL': 'C'})
    except subprocess.TimeoutExpired as failure:
        forward_stderr(failure.stderr)
        raise TimeoutError(_("Snapshot command exceeded its time limit.")) from None
    forward_stderr(result.stderr)
    if result.returncode:
        raise RuntimeError(_("Snapshot command failed; inspect the external tool diagnostics."))
    return result.stdout


def machine_id():
    value = Path('/etc/machine-id').read_text().strip()
    if not ID.fullmatch(value):
        raise RuntimeError(_("The capture source has no valid machine identity."))
    return value


def coverage(paths, roots, barriers):
    used = set()
    for value in paths:
        path = Path(value)
        containing = [name for name, root in roots.items() if path.is_relative_to(root)]
        if not containing:
            raise ValueError(_("An application path has no declared snapshot root."))
        selected = max(containing, key=lambda name: len(Path(roots[name]).parts))
        used.add(selected)
        used.update(name for name, root in roots.items() if Path(root).is_relative_to(path))
        for barrier in map(Path, barriers):
            if (barrier.is_relative_to(path) and str(barrier) not in roots.values()
                    or path.is_relative_to(barrier) and not Path(roots[selected]).is_relative_to(barrier)):
                raise ValueError(_("Application data crosses an undeclared mount or nested subvolume."))
    return sorted(used)


def filesystem(path, *, timeout=120):
    data = json.loads(run(['findmnt', '--json', '--target', str(path), '--output', 'TARGET,FSTYPE,FSROOT,UUID'],
                          timeout=timeout() if callable(timeout) else timeout))
    rows = data.get('filesystems', [])
    if len(rows) != 1 or rows[0].get('fstype') != 'btrfs' or not rows[0].get('uuid'):
        raise ValueError(_("Snapshot capture requires Btrfs at every declared source and destination."))
    row = rows[0]
    row['filesystemPath'] = str(Path(row['fsroot']) / Path(path).relative_to(row['target']))
    return row


def mountpoints():
    # Mountinfo has octal-escaped paths and also covers bind mounts.
    return [re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), line.split()[4])
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def subvolume(path, *, timeout=120):
    fields = dict(line.strip().split(':', 1) for line in run(
        ['btrfs', 'subvolume', 'show', str(path)], timeout=timeout).splitlines() if ':' in line)
    identity = fields.get('UUID', '').strip()
    if not UUID.fullmatch(identity):
        raise ValueError(_("Cannot verify the source subvolume identity."))
    return {'uuid': identity, 'parent': fields.get('Parent UUID', '').strip()}


def inspect(settings, *, timeout=120):
    def command(arguments):
        return run(arguments, timeout=timeout() if callable(timeout) else timeout)

    paths = sorted({path for app in settings['applications'].values() for path in app['paths']})
    roots = {name: root['path'] for name, root in settings['roots'].items()}
    used = coverage(paths, roots, mountpoints())
    topology = {'roots': {}, 'paths': []}
    nested = []
    for name in used:
        root = settings['roots'][name]
        source = Path(root['path'])
        if source.resolve(strict=True) != source:
            raise ValueError(_("Snapshot source roots must not contain symbolic links."))
        fs = filesystem(source, timeout=timeout)
        details = command(['btrfs', 'subvolume', 'show', str(source)])
        identities = [line.split(':', 1)[1].strip() for line in details.splitlines()
                      if line.strip().startswith('UUID:')]
        if len(identities) != 1 or not UUID.fullmatch(identities[0]):
            raise ValueError(_("Cannot verify the source subvolume identity."))
        destination = Path(root['snapshotDirectory'])
        ancestor = destination
        while not ancestor.exists() and not ancestor.is_symlink():
            ancestor = ancestor.parent
        if ancestor.resolve(strict=True) != ancestor:
            raise ValueError(_("Snapshot storage must not contain symbolic links."))
        storage = filesystem(ancestor, timeout=timeout)
        store_path = Path(storage['filesystemPath']) / destination.relative_to(ancestor)
        if fs['uuid'] != storage['uuid'] or store_path.is_relative_to(fs['filesystemPath']):
            raise ValueError(_("Snapshot storage must be on the same Btrfs filesystem outside the captured subvolume."))
        free = os.statvfs(source)
        if free.f_bavail * free.f_frsize < settings['minFreeBytes']:
            raise RuntimeError(_("Insufficient free space for snapshot capture."))
        listing = command(['btrfs', 'subvolume', 'list', '-o', str(source)])
        for line in listing.splitlines():
            if ' path ' not in line:
                raise ValueError(_("Cannot inspect nested Btrfs subvolumes safely."))
            relative = Path('/' + line.split(' path ', 1)[1])
            if relative.is_relative_to(fs['filesystemPath']):
                nested.append(str(source / relative.relative_to(fs['filesystemPath'])))
        topology['roots'][name] = {**root, 'filesystemUuid': fs['uuid'], 'subvolumeUuid': identities[0]}
    coverage(paths, roots, nested)
    for path in paths:
        actual = Path(path)
        resolved = actual.resolve(strict=True)
        if actual.parent.resolve(strict=True) != actual.parent:
            raise ValueError(_("Application paths must not traverse symbolic-link directories."))
        if actual.is_symlink() and not any(resolved.is_relative_to(other) for other in paths if other != path):
            raise ValueError(_("Include the resolved target of each application symbolic link explicitly."))
        containing = [name for name in used if actual.is_relative_to(roots[name])]
        selected = max(containing, key=lambda name: len(Path(roots[name]).parts))
        topology['paths'].append({'source': path, 'root': selected,
                                  'relative': str(actual.relative_to(roots[selected]))})
        for name in used:
            if roots[name] != path and Path(roots[name]).is_relative_to(actual):
                item = {'source': roots[name], 'root': name, 'relative': '.'}
                if item not in topology['paths']:
                    topology['paths'].append(item)
    return topology


def secure_directory(path, *, create=False, private=True):
    path = Path(path)
    for component in reversed([path, *path.parents]):
        if component.is_symlink():
            raise ValueError(_("Capture state directories must not contain symbolic links."))
        if not component.exists():
            if not create:
                continue
            component.mkdir(mode=0o700)
        entry = component.stat()
        if (not stat.S_ISDIR(entry.st_mode) or entry.st_uid not in (0, OWNER_UID)
                or entry.st_mode & 0o022 and not (entry.st_uid == 0 and entry.st_mode & stat.S_ISVTX)):
            raise ValueError(_("Capture state directories require trusted ownership and private write access."))
    if private and path.exists() and (path.stat().st_uid != OWNER_UID or path.stat().st_mode & 0o077):
        raise ValueError(_("Capture state directories must be private and owned by root."))
    return path


def write_record(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_record(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        entry = os.fstat(stream.fileno())
        if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != OWNER_UID
                or entry.st_mode & 0o077 or entry.st_size > 1024 * 1024):
            raise ValueError(_("Unsafe capture state file."))
        return json.load(stream)


@contextlib.contextmanager
def lock(settings):
    state = secure_directory(settings['stateDirectory'], create=True)
    descriptor = os.open(state / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        entry = os.fstat(descriptor)
        if not stat.S_ISREG(entry.st_mode) or entry.st_uid != OWNER_UID or entry.st_mode & 0o077:
            raise ValueError(_("Unsafe capture state file."))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(_("Another snapshot operation holds the capture lock.")) from None
        yield state
    finally:
        os.close(descriptor)


def writers(settings):
    return sorted({unit for app in settings['applications'].values() for unit in app['stopUnits']})


def service_state(unit, *, timeout=120):
    fields = dict(line.split('=', 1) for line in run(
        ['systemctl', 'show', '--property=LoadState,ActiveState', unit], timeout=timeout).splitlines() if '=' in line)
    if fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in ('active', 'inactive', 'failed'):
        raise RuntimeError(_("A capture writer is missing or changing state; no consistent capture is available."))
    return fields['ActiveState']


def pending(settings, state):
    path = state / 'pending.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = read_record(path)
    if (not isinstance(value, dict) or set(value) != {'version', 'machineId', 'snapshotId', 'activeUnits'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value.get('snapshotId'), str) or not ID.fullmatch(value['snapshotId'])
            or value.get('machineId') != machine_id()):
        raise RuntimeError(_("Capture recovery state does not match this machine identity."))
    active = value['activeUnits']
    if (not isinstance(active, list) or any(not isinstance(unit, str) for unit in active)
            or len(set(active)) != len(active) or any(unit not in writers(settings) for unit in active)):
        raise RuntimeError(_("Capture recovery names writers outside the current configuration."))
    return value


def resume_locked(settings, state):
    value = pending(settings, state)
    active = value['activeUnits'] if value else []
    if active:
        run(['systemctl', 'start', *active])
        if any(service_state(unit) != 'active' for unit in active):
            raise RuntimeError(_("Capture writers did not resume; retain the recovery record."))
    if value:
        (state / 'pending.json').unlink()
        sync_directory(state)
    return {'applied': True, 'resumedUnits': active}


def resume(settings, *, apply=False):
    settings = policy(settings)
    state = secure_directory(settings['stateDirectory'])
    value = pending(settings, state) if state.exists() else None
    if apply:
        # Activation and snapshot upload can hold the lease without stopped
        # writers. A boot/stop-post recovery then has nothing to recover.
        if value is None:
            return {'applied': True, 'resumedUnits': []}
        with lock(settings) as state:
            return resume_locked(settings, state)
    return {'applied': False, 'resumedUnits': value['activeUnits'] if value else []}


def cold_identity(settings):
    paths = sorted({path for app in settings['applications'].values() for path in app['paths']})
    result = []
    for path in paths:
        source = Path(path)
        resolved = source.resolve(strict=True)
        if source.parent.resolve(strict=True) != source.parent:
            raise ValueError(_("Application paths must not traverse symbolic-link directories."))
        if source.is_symlink() and not any(resolved.is_relative_to(other) for other in paths if other != path):
            raise ValueError(_("Include the resolved target of each application symbolic link explicitly."))
        for mount in mountpoints():
            if Path(mount).is_relative_to(source) and mount not in paths:
                raise ValueError(_("Declare mounted application data as an explicit backup path."))
        entry = source.lstat()
        fs = json.loads(run(['findmnt', '--json', '--target', path, '--output', 'TARGET,SOURCE,FSTYPE,UUID,FSROOT']))['filesystems']
        if len(fs) != 1 or fs[0].get('fstype') not in ('ext4', 'btrfs'):
            raise ValueError(_("Cold capture requires ext4 or Btrfs application data."))
        result.append({'path': path, 'device': entry.st_dev, 'inode': entry.st_ino,
                       'type': stat.S_IFMT(entry.st_mode), 'filesystem': fs[0]})
    return result


@contextlib.contextmanager
def cold(settings, state):
    """Caller holds the capture lock until upload and writer recovery finish."""
    if pending(settings, state):
        raise RuntimeError(_("Interrupted capture needs writer recovery before another snapshot."))
    before = cold_identity(settings)
    configured = writers(settings)
    active = [unit for unit in configured if service_state(unit) == 'active']
    write_record(state / 'pending.json', {'version': 1, 'machineId': machine_id(),
                 'snapshotId': uuid.uuid4().hex, 'activeUnits': active})
    try:
        if configured:
            run(['systemctl', 'stop', *configured], timeout=settings['timeoutSeconds'])
        def stopped():
            if any(service_state(unit) not in ('inactive', 'failed') for unit in configured):
                raise RuntimeError(_("Capture writers did not stop; refusing to publish a snapshot."))
        stopped()
        if cold_identity(settings) != before:
            raise RuntimeError(_("Snapshot source identity changed after writer shutdown."))
        yield
        stopped()
        if cold_identity(settings) != before:
            raise RuntimeError(_("Snapshot source identity changed after writer shutdown."))
    finally:
        resume_locked(settings, state)


def running_system():
    return str(Path('/run/current-system').resolve(strict=True))


def capture(settings, *, apply=False, expected_system=None):
    expected = (expected_system if expected_system is not None else running_system()) if apply else None
    settings = policy(settings)
    topology = inspect(settings)
    if not apply:
        state = secure_directory(settings['stateDirectory'])
        if state.exists() and pending(settings, state):
            raise RuntimeError(_("Interrupted capture needs writer recovery before another snapshot."))
        return {'applied': False, **topology, 'writers': writers(settings)}
    with lock(settings) as state:
        if running_system() != expected:
            raise RuntimeError(_("The running system changed before snapshot capture."))
        return capture_locked(settings, state, topology)


def capture_locked(settings, state, topology):
    if pending(settings, state):
        raise RuntimeError(_("Interrupted capture needs writer recovery before another snapshot."))
    records = secure_directory(state / 'records', create=True)
    identifier = uuid.uuid4().hex
    record = {'version': 1, 'snapshotId': identifier, 'machineId': machine_id(),
              'createdAt': int(time.time()), 'state': 'capturing', **topology}
    destinations = {}
    containers = set()
    for name, root in topology['roots'].items():
        base = secure_directory(root['snapshotDirectory'], create=True)
        directory = base / identifier
        if directory not in containers:
            directory.mkdir(mode=0o700)
            containers.add(directory)
        destinations[name] = str(directory / name)
        root['snapshot'] = destinations[name]
    write_record(records / (identifier + '.json'), record)
    deadline = time.monotonic() + settings['timeoutSeconds']

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError(_("Snapshot capture exceeded its configured time limit."))
        return seconds

    active = [unit for unit in writers(settings) if service_state(unit, timeout=remaining()) == 'active']
    write_record(state / 'pending.json', {'version': 1, 'machineId': record['machineId'],
                 'snapshotId': identifier, 'activeUnits': active})
    try:
        try:
            configured = writers(settings)
            if configured:
                # Stop even initially inactive units to cancel intervening
                # activation jobs; resume still uses only the original set.
                run(['systemctl', 'stop', *configured], timeout=remaining())

            def require_stopped():
                if any(service_state(unit, timeout=remaining()) not in ('inactive', 'failed') for unit in configured):
                    raise RuntimeError(_("Capture writers did not stop; refusing to publish a snapshot."))

            require_stopped()
            current = inspect(settings, timeout=remaining)
            # Snapshot destinations are operation metadata, not source identity.
            original = {'paths': topology['paths'], 'roots': {
                name: {key: value for key, value in root.items() if key != 'snapshot'}
                for name, root in topology['roots'].items()}}
            if current != original:
                raise RuntimeError(_("Snapshot source identity changed after writer shutdown."))
            for name, root in topology['roots'].items():
                require_stopped()
                run(['btrfs', 'subvolume', 'snapshot', '-r', root['path'], destinations[name]], timeout=remaining())
                if run(['btrfs', 'property', 'get', '-ts', destinations[name], 'ro'], timeout=remaining()).strip() != 'ro=true':
                    raise RuntimeError(_("The captured subvolume is not read-only."))
                identity = subvolume(destinations[name], timeout=remaining())
                if identity['parent'] != root['subvolumeUuid']:
                    raise RuntimeError(_("Captured snapshot identity does not match its declared source."))
                root['snapshotUuid'] = identity['uuid']
                write_record(records / (identifier + '.json'), record)
            require_stopped()
        finally:
            resume_locked(settings, state)
    except BaseException:
        record['state'] = 'failed'
        write_record(records / (identifier + '.json'), record)
        raise
    record['state'] = 'ready'
    write_record(records / (identifier + '.json'), record)
    return {'applied': True, **record}


def inventory(settings):
    settings = policy(settings)
    state = secure_directory(settings['stateDirectory'])
    records = state / 'records'
    result = []
    if records.exists() or records.is_symlink():
        secure_directory(records)
        for path in sorted(records.iterdir()):
            if not re.fullmatch(r'[0-9a-f]{32}\.json', path.name):
                raise ValueError(_("Unexpected file in snapshot inventory."))
            value = read_record(path)
            validate_record(value, path.stem)
            if value['state'] == 'ready':
                result.append(value)
    return {'snapshots': result}


def validate_record(value, identifier, *, expected_machine_id=None):
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError(_("Invalid snapshot inventory record."))
    fields = {'version', 'snapshotId', 'machineId', 'createdAt', 'state', 'roots', 'paths'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value.get('version')) is not int or value['version'] != 1
            or value.get('snapshotId') != identifier
            or value.get('machineId') != (machine_id() if expected_machine_id is None else expected_machine_id)
            or type(value.get('createdAt')) is not int or value['createdAt'] < 0
            or value.get('state') not in ('capturing', 'failed', 'ready', 'deleting', 'deleted')
            or not isinstance(value.get('roots'), dict) or not value['roots']
            or not isinstance(value.get('paths'), list) or not value['paths']):
        raise ValueError(_("Invalid snapshot inventory record."))
    for name, root in value['roots'].items():
        required = {'path', 'snapshotDirectory', 'filesystemUuid', 'subvolumeUuid', 'snapshot'}
        if (not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(root, dict)
                or set(root) - (required | {'snapshotUuid'}) or not required.issubset(root)
                or value['state'] == 'ready' and 'snapshotUuid' not in root):
            raise ValueError(_("Invalid snapshot inventory record."))
        absolute(root['path'], root=True)
        absolute(root['snapshotDirectory'])
        if root['snapshot'] != str(Path(root['snapshotDirectory']) / identifier / name):
            raise ValueError(_("Invalid snapshot inventory record."))
        for field in ('filesystemUuid', 'subvolumeUuid', *(['snapshotUuid'] if 'snapshotUuid' in root else [])):
            if not isinstance(root[field], str) or not UUID.fullmatch(root[field]):
                raise ValueError(_("Invalid snapshot inventory record."))
    for item in value['paths']:
        if (not isinstance(item, dict) or set(item) != {'source', 'root', 'relative'}
                or not isinstance(item.get('root'), str) or item['root'] not in value['roots']
                or not isinstance(item.get('relative'), str) or Path(item['relative']).is_absolute()
                or '..' in Path(item['relative']).parts):
            raise ValueError(_("Invalid snapshot inventory record."))
        absolute(item['source'])
        if str(Path(value['roots'][item['root']]['path']) / item['relative']) != item['source']:
            raise ValueError(_("Invalid snapshot inventory record."))


def removal_record(settings, identifier):
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError(_("Select an explicit snapshot ID from the local snapshot inventory."))
    state = secure_directory(settings['stateDirectory'])
    secure_directory(state / 'records')
    value = read_record(state / 'records' / (identifier + '.json'))
    validate_record(value, identifier)
    if value['state'] == 'deleted':
        raise ValueError(_("Invalid snapshot inventory record."))
    if pending(settings, state):
        raise RuntimeError(_("Interrupted capture needs writer recovery before another snapshot."))
    for name, root in value['roots'].items():
        selected = settings['roots'].get(name)
        if (selected is None or not isinstance(root, dict)
                or any(root.get(key) != selected[key] for key in ('path', 'snapshotDirectory'))
                or root.get('snapshot') != str(Path(selected['snapshotDirectory']) / identifier / name)):
            raise ValueError(_("Snapshot record does not match the configured source and storage paths."))
        container = secure_directory(Path(selected['snapshotDirectory']) / identifier)
        path = container / name
        if not path.exists() and not path.is_symlink() and value['state'] in ('capturing', 'failed', 'deleting'):
            continue
        verify_removal(root)
    return value


def verify_removal(root):
    path = Path(root['snapshot'])
    if path.is_symlink() or not path.exists():
        raise RuntimeError(_("Snapshot removal identity check failed; retain uncertain remnants."))
    identity = subvolume(path)
    fs = filesystem(path)
    if (identity['uuid'] != root.get('snapshotUuid') or identity['parent'] != root.get('subvolumeUuid')
            or fs['uuid'] != root.get('filesystemUuid')
            or run(['btrfs', 'property', 'get', '-ts', str(path), 'ro']).strip() != 'ro=true'
            or mounted_snapshot(fs) or any(Path(mount).is_relative_to(path) for mount in mountpoints())):
        raise RuntimeError(_("Snapshot removal identity check failed; retain uncertain remnants."))


def mounted_snapshot(fs):
    rows = json.loads(run(['findmnt', '--json', '--list', '--output', 'FSTYPE,UUID,FSROOT,TARGET']))['filesystems']
    return any(row.get('fstype') == 'btrfs' and row.get('uuid') == fs['uuid']
               and Path(row['fsroot']).is_relative_to(fs['filesystemPath']) for row in rows)


def remove(settings, identifier, *, apply=False):
    settings = policy(settings)
    if not apply:
        value = removal_record(settings, identifier)
        return {'applied': False, 'snapshotId': identifier, 'roots': value['roots']}
    with lock(settings) as state:
        return remove_locked(settings, state, identifier)


def remove_locked(settings, state, identifier):
    value = removal_record(settings, identifier)
    value['state'] = 'deleting'
    write_record(state / 'records' / (identifier + '.json'), value)
    for root in value['roots'].values():
        path = Path(root['snapshot'])
        if path.exists() or path.is_symlink():
            verify_removal(root)
            run(['btrfs', 'subvolume', 'delete', '--commit-after', str(path)])
    # rmdir never removes unexpected files or other captures.
    for container in {Path(root['snapshot']).parent for root in value['roots'].values()}:
        if container.exists():
            container.rmdir()
    value['state'] = 'deleted'
    write_record(state / 'records' / (identifier + '.json'), value)
    return {'applied': True, 'snapshotId': identifier, 'state': 'deleted'}


def main(argv=None):
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument('action', choices=['snapshot', 'snapshots', 'snapshot-remove', 'resume'])
    parser.add_argument('--config', default='/etc/nixberry/capture.json', help=_("Configuration-owned capture policy file"))
    parser.add_argument('--json', action='store_true', help=_("Print technical capture metadata as JSON"))
    parser.add_argument('--snapshot', help=_("Explicit local Btrfs snapshot ID"))
    parser.add_argument('--system', help=_("Expected running NixOS system generation"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true', help=_("Apply snapshot capture or writer recovery"))
    modes.add_argument('--dry-run', action='store_true', help=_("Inspect capture readiness without changing files or services"))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RuntimeError(_("Snapshot operations require root on the declared source."))
    if args.action == 'snapshots' and args.apply:
        parser.error(_("Snapshot inventory is read-only."))
    if args.snapshot is not None and args.action != 'snapshot-remove':
        parser.error(_("--snapshot only applies to snapshot-remove in the capture tool."))
    if args.system is not None and args.action != 'snapshot':
        parser.error(_("--system only applies to snapshot in the capture tool."))

    def interrupted(signum, frame):
        raise InterruptedError(_("Snapshot capture was interrupted; checking writer recovery."))

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    expected = (args.system or running_system()) if args.action == 'snapshot' and args.apply else None
    settings = policy(json.loads(Path(args.config).read_text()))
    if args.action == 'snapshot':
        result = capture(settings, apply=args.apply, expected_system=expected)
    elif args.action == 'resume':
        result = resume(settings, apply=args.apply)
    elif args.action == 'snapshot-remove':
        result = remove(settings, args.snapshot, apply=args.apply)
    else:
        result = inventory(settings)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
        error("{error}", error=str(failure))
        raise SystemExit(1)
