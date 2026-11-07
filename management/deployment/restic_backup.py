"""Back up configuration-owned application data directly into an encrypted Restic repository."""
import backup_identity
import hashlib
import contextlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from messages import _, ArgumentParser, error, forward_stderr
import snapshots
import recovery_recipe

ID = re.compile(r'[0-9a-f]{64}')
FORMAT = 'nixberry-restic-v2'
FORMATS = ('nixberry-restic-v1', FORMAT)
RECEIPT = 'nixberry-receipt-v1'
SYSTEM = re.compile(r'/nix/store/[a-z0-9]{32}-nixos-system-[A-Za-z0-9+._-]+')


def runtime_path(value):
    snapshots.absolute(value)
    if any(Path(value).resolve().is_relative_to(path) for path in ('/nix/store', '/proc', '/sys', '/dev')):
        raise ValueError(_("Restic runtime paths must stay outside the Nix store and device filesystems."))
    return value


def policy(value):
    required = {'version', 'sourceId', 'captureMethod', 'capture', 'repository', 'passwordFile',
                'repositoryIdFile', 'recoveryFile', 'runtimeDirectory', 'timeoutSeconds', 'ssh'}
    if (not isinstance(value, dict) or set(value) != required or type(value['version']) is not int
            or value['version'] != 1 or not isinstance(value['sourceId'], str)
            or not re.fullmatch(r'[A-Za-z0-9:_.-]{1,128}', value['sourceId'])
            or value['captureMethod'] not in ('snapshot', 'cold')
            or type(value['timeoutSeconds']) is not int or not 1 <= value['timeoutSeconds'] <= 86400):
        raise ValueError(_("Invalid Restic backup policy."))
    snapshots.policy(value['capture'])
    for key in ('passwordFile', 'repositoryIdFile', 'recoveryFile', 'runtimeDirectory'):
        runtime_path(value[key])
    storage_policy(value)
    paths = [Path(path) for app in value['capture']['applications'].values() for path in app['paths']]
    internal = [value['runtimeDirectory'], value['recoveryFile'], value['passwordFile'], value['repositoryIdFile']]
    if value['ssh'] is not None:
        internal += [value['ssh']['identityFile'], value['ssh']['knownHostsFile']]
    if value['repository'].startswith('/'):
        internal.append(value['repository'])
    internal = [Path(path) for path in internal]
    variants = ((paths, internal), ([path.resolve() for path in paths], [path.resolve() for path in internal]))
    if any(a.is_relative_to(b) or b.is_relative_to(a)
           for sources, destinations in variants for a in sources for b in destinations):
        raise ValueError(_("Backup storage, runtime files and credentials must stay outside application data paths."))
    return value


def storage_policy(value):
    if (not isinstance(value['sourceId'], str) or not re.fullmatch(r'[A-Za-z0-9:_.-]{1,128}', value['sourceId'])
            or type(value['timeoutSeconds']) is not int or not 1 <= value['timeoutSeconds'] <= 86400):
        raise ValueError(_("Invalid Restic backup policy."))
    for key in ('passwordFile', 'runtimeDirectory'):
        runtime_path(value[key])
    repository = value['repository']
    if not isinstance(repository, str):
        raise ValueError(_("Restic requires an absolute local repository or an explicitly pinned SFTP repository."))
    if repository.startswith('/'):
        runtime_path(repository)
        if value['ssh'] is not None:
            raise ValueError(_("Local Restic repositories do not accept SFTP settings."))
    else:
        if not re.fullmatch(r'sftp:[A-Za-z_][A-Za-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.-]*:/[^\x00-\x1f\x7f]+', repository):
            raise ValueError(_("Restic requires an absolute local repository or an explicitly pinned SFTP repository."))
        snapshots.absolute(repository.split(':', 2)[2])
        ssh = value['ssh']
        if (not isinstance(ssh, dict) or set(ssh) != {'port', 'identityFile', 'knownHostsFile'}
                or type(ssh['port']) is not int or not 1 <= ssh['port'] <= 65535):
            raise ValueError(_("SFTP backups require an explicit SSH key, host-key file and port."))
        runtime_path(ssh['identityFile'])
        runtime_path(ssh['knownHostsFile'])
    return value


def private_file(path, *, private=True, limit=128 * 1024 * 1024, owner_uid=None):
    owner_uid = snapshots.OWNER_UID if owner_uid is None else owner_uid
    snapshots.secure_directory(Path(path).parent, private=False, owner_uid=owner_uid)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        entry = os.fstat(stream.fileno())
        if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != owner_uid
                or entry.st_mode & (0o077 if private else 0o022) or entry.st_size > limit):
            raise ValueError(_("Restic runtime files require trusted ownership, safe permissions and bounded size."))
        contents = stream.read(limit + 1)
        if len(contents) > limit:
            raise ValueError(_("Restic runtime files require trusted ownership, safe permissions and bounded size."))
        return contents


def require_tmpfs(path):
    result = snapshots.run(['findmnt', '--noheadings', '--output', 'FSTYPE', '--target', str(path)])
    if result.strip() != 'tmpfs':
        raise ValueError(_("Restic temporary data must use a tmpfs runtime directory."))


class Restic:
    def __init__(self, settings, *, owner_uid=None, repository_id=None, read_only=False):
        self.settings = settings
        self.owner_uid = owner_uid
        self.repository_id = repository_id
        self.read_only = read_only
        self.deadline = time.monotonic() + settings['timeoutSeconds']

    def command(self):
        value = self.settings
        command = ['restic', '--repo', value['repository'], '--password-file', value['passwordFile'], '--no-cache', '--json']
        if self.read_only:
            command.append('--no-lock')
        if value['ssh'] is not None:
            ssh = value['ssh']
            user_host = value['repository'].removeprefix('sftp:').split(':', 1)[0]
            transport = ['ssh', '-F', '/dev/null', '-p', str(ssh['port']), '-i', ssh['identityFile'],
                         '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
                         '-o', 'IdentityAgent=none', '-o', 'ForwardAgent=no', '-o', 'GlobalKnownHostsFile=/dev/null',
                         '-o', 'UserKnownHostsFile=' + ssh['knownHostsFile'], '-o', 'ConnectTimeout=10',
                         '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-s', user_host, 'sftp']
            command += ['--option', 'sftp.command=' + shlex.join(transport)]
        return command

    def run(self, arguments, *, input=None, binary=False, discard=False, max_output=None):
        statistics = (len(arguments) == 4 and arguments[:3] == ['stats', '--mode', 'restore-size']
                      and isinstance(arguments[3], str) and ID.fullmatch(arguments[3]))
        if self.read_only and (not arguments or arguments[0] not in ('snapshots', 'dump')
                              and arguments != ['cat', 'config'] and not statistics):
            raise ValueError(_("Independent backup access permits only repository identity, inventory and payload reads."))
        return self._execute(arguments, input=input, binary=binary, discard=discard, max_output=max_output)

    def restore(self, identifier, destination, paths):
        if not self.read_only or not paths or not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise ValueError(_("Payload restoration requires a read-only reader and a full snapshot ID."))
        arguments = ['restore', identifier, '--target', str(destination), '--verify', '--sparse']
        for path in sorted({item['payload'] for item in paths}):
            snapshots.absolute(path)
            arguments += ['--include', literal_pattern(path)]
        for hidden in hidden_payload_paths(paths):
            # Restore forbids combining include and exclude flags. Ordered
            # negative include patterns subtract hidden parent underlays.
            arguments += ['--include', '!' + literal_pattern(hidden)]
        return self._execute(arguments, discard=True)

    def _execute(self, arguments, *, input=None, binary=False, discard=False, max_output=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(_("Restic exceeded its configured time limit."))
        environment = {key: value for key, value in os.environ.items() if not key.startswith('RESTIC_')}
        environment.update(TMPDIR=self.settings['runtimeDirectory'], LC_ALL='C')
        if max_output is None and self.read_only and not discard:
            max_output = 16 * 1024 * 1024
        try:
            # SFTP StartForeground uses /dev/tty even with redirected standard
            # streams. Detach unattended jobs from the caller's terminal.
            with contextlib.ExitStack() as stack:
                options = {}
                output = None
                if max_output is not None:
                    import resource
                    directory = snapshots.secure_directory(self.settings['runtimeDirectory'], create=True, owner_uid=self.owner_uid)
                    require_tmpfs(directory)
                    output = stack.enter_context(tempfile.TemporaryFile(dir=directory))
                    def bounded_file():
                        resource.setrlimit(resource.RLIMIT_FSIZE, (max_output, max_output))
                    options['preexec_fn'] = bounded_file
                result = subprocess.run(self.command() + arguments, input=input,
                                        stdin=subprocess.DEVNULL if input is None else None,
                                        start_new_session=True,
                                        stdout=output if output is not None else subprocess.DEVNULL if discard else subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=not binary,
                                        timeout=remaining, env=environment, **options)
                if output is not None:
                    output.seek(0)
                    contents = output.read(max_output + 1)
                    result.stdout = contents if binary else contents.decode('utf-8')
        except subprocess.TimeoutExpired as failure:
            forward_stderr(failure.stderr)
            raise TimeoutError(_("Restic exceeded its configured time limit.")) from None
        forward_stderr(result.stderr)
        if result.returncode:
            raise RuntimeError(_("Restic command failed; inspect retained repository state before retrying."))
        return result.stdout

    def repository(self):
        value = self.settings
        private_file(value['passwordFile'], owner_uid=self.owner_uid)
        expected = self.repository_id if self.repository_id is not None else private_file(
            value['repositoryIdFile'], private=False, limit=1024, owner_uid=self.owner_uid).decode().strip()
        if not ID.fullmatch(expected):
            raise ValueError(_("The pinned Restic repository identity is invalid."))
        if value['ssh'] is not None:
            private_file(value['ssh']['identityFile'], owner_uid=self.owner_uid)
            private_file(value['ssh']['knownHostsFile'], private=False, owner_uid=self.owner_uid)
            self.transport()
        actual = json.loads(self.run(['cat', 'config']))
        if not isinstance(actual, dict) or actual.get('id') != expected:
            raise RuntimeError(_("The Restic repository identity does not match its configured pin."))

    def transport(self):
        # Restic retries backend open failures, including SSH authentication and
        # host-key failures. An SFTP-only probe fails promptly before any outage.
        value = self.settings
        ssh = value['ssh']
        host = value['repository'].removeprefix('sftp:').split(':', 1)[0]
        command = ['sftp', '-b', '-', '-F', '/dev/null', '-P', str(ssh['port']), '-i', ssh['identityFile'],
                   '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
                   '-o', 'IdentityAgent=none', '-o', 'ForwardAgent=no', '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'UserKnownHostsFile=' + ssh['knownHostsFile'], '-o', 'ConnectTimeout=10', host]
        try:
            result = subprocess.run(command, input='pwd\n', text=True, capture_output=True,
                                    timeout=min(30, max(0.01, self.deadline - time.monotonic())))
        except subprocess.TimeoutExpired as failure:
            forward_stderr(failure.stderr)
            raise TimeoutError(_("Storage SFTP readiness check exceeded its time limit.")) from None
        forward_stderr(result.stderr)
        if result.returncode:
            raise RuntimeError(_("Storage SFTP trust or authentication failed before writer shutdown."))


def completed(output):
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    identifiers = [row.get('snapshot_id') for row in rows if row.get('message_type') == 'summary']
    if len(identifiers) != 1 or not isinstance(identifiers[0], str) or not ID.fullmatch(identifiers[0]):
        raise RuntimeError(_("Restic did not return one complete snapshot identity."))
    return identifiers[0]


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n'


def running_system():
    value = str(Path('/run/current-system').resolve(strict=True))
    if not SYSTEM.fullmatch(value):
        raise RuntimeError(_("Recovery configuration requires a valid running NixOS system generation."))
    return value


def read_archive(stream, *, timeout=120):
    deadline = time.monotonic() + timeout
    chunks, total = [], 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream.fileno()], [], [], remaining)[0]:
            raise TimeoutError(_("Recovery archive transfer exceeded its time limit."))
        chunk = os.read(stream.fileno(), 64 * 1024)
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > recovery_recipe.MAX_ARCHIVE:
            raise ValueError(_("Recovery recipe archive exceeds its size limit."))
        chunks.append(chunk)


def validate_recipe(archive, settings, system):
    record = recovery_recipe.validate(archive)
    if (record['sourceId'] != settings['sourceId'] or record['systemPath'] != system
            or record['policySha256'] != hashlib.sha256(encode(settings).encode()).hexdigest()):
        raise ValueError(_("The received recovery archive belongs to a different source, system or policy."))


def stage(value, expected_system, *, archive=None, expected_sha256=None, apply=False):
    settings = policy(value)
    if not isinstance(expected_system, str) or not SYSTEM.fullmatch(expected_system):
        raise ValueError(_("Recovery staging requires the explicit expected NixOS system path."))
    if running_system() != expected_system:
        raise RuntimeError(_("The running system does not match the recovery configuration being staged."))
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not ID.fullmatch(expected_sha256):
            raise ValueError(_("Recovery transfer requires a full SHA256 archive identity."))
        if apply and (not isinstance(archive, bytes) or hashlib.sha256(archive).hexdigest() != expected_sha256):
            raise ValueError(_("The received recovery archive does not match its expected SHA256 identity."))
        if apply:
            validate_recipe(archive, settings, expected_system)
    elif archive is not None:
        raise ValueError(_("Recovery transfer requires a full SHA256 archive identity."))
    else:
        archive = private_file(settings['recoveryFile'])
        validate_recipe(archive, settings, expected_system)
    metadata = {'version': 1, 'sourceId': settings['sourceId'], 'systemPath': expected_system,
                'policySha256': hashlib.sha256(encode(settings).encode()).hexdigest(),
                'archiveSha256': expected_sha256 or hashlib.sha256(archive).hexdigest()}
    if not apply:
        return {'applied': False, **metadata}
    with snapshots.lock(settings['capture']) as state:
        if snapshots.pending(settings['capture'], state):
            raise RuntimeError(_("Interrupted capture needs writer recovery before another snapshot."))
        if running_system() != expected_system:
            raise RuntimeError(_("Recovery configuration changed while staging; no binding was published."))
        if expected_sha256 is not None:
            destination = Path(settings['recoveryFile'])
            snapshots.secure_directory(destination.parent, create=True)
            if destination.exists() or destination.is_symlink():
                private_file(destination)
            temporary = destination.with_name('.' + destination.name + '-' + uuid.uuid4().hex)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(archive)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                snapshots.sync_directory(destination.parent)
            finally:
                temporary.unlink(missing_ok=True)
        if (running_system() != expected_system
                or hashlib.sha256(private_file(settings['recoveryFile'])).hexdigest() != metadata['archiveSha256']):
            raise RuntimeError(_("Recovery configuration changed while staging; no binding was published."))
        snapshots.write_record(settings['recoveryFile'] + '.json', metadata)
    return {'applied': True, **metadata}


def recovery(settings):
    try:
        archive = private_file(settings['recoveryFile'])
        metadata = json.loads(private_file(settings['recoveryFile'] + '.json', limit=4096))
    except FileNotFoundError:
        raise RuntimeError(_("Recovery configuration is missing; provision the recipe archive and run backup-stage.")) from None
    expected = {'version': 1, 'sourceId': settings['sourceId'], 'systemPath': running_system(),
                'policySha256': hashlib.sha256(encode(settings).encode()).hexdigest(),
                'archiveSha256': hashlib.sha256(archive).hexdigest()}
    if metadata != expected or type(metadata.get('version')) is not int:
        raise RuntimeError(_("The staged recovery configuration is stale or does not match this backup policy."))
    validate_recipe(archive, settings, expected['systemPath'])
    return metadata


def initialize(value, *, apply=False):
    settings = policy(value)
    destination = Path(settings['repositoryIdFile'])
    if destination.exists() or destination.is_symlink():
        raise ValueError(_("Repository initialization requires a new repository identity file."))
    private_file(settings['passwordFile'])
    if settings['ssh'] is not None:
        private_file(settings['ssh']['identityFile'])
        private_file(settings['ssh']['knownHostsFile'], private=False)
    if not apply:
        return {'applied': False, 'repository': settings['repository'], 'repositoryIdFile': str(destination)}
    runtime = snapshots.secure_directory(settings['runtimeDirectory'], create=True)
    require_tmpfs(runtime)
    snapshots.secure_directory(destination.parent, create=True)
    restic = Restic(settings)
    if settings['ssh'] is not None:
        restic.transport()
    restic.run(['init'])
    config = json.loads(restic.run(['cat', 'config']))
    identity = config.get('id') if isinstance(config, dict) else None
    if not isinstance(identity, str) or not ID.fullmatch(identity):
        raise ValueError(_("The pinned Restic repository identity is invalid."))
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(identity + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    snapshots.sync_directory(destination.parent)
    return {'applied': True, 'repositoryId': identity}


def hidden_payload_paths(paths):
    """A separate child capture replaces its hidden view in ancestor snapshots."""
    hidden = set()
    for parent in paths:
        source = Path(parent['source'])
        for child in paths:
            nested = Path(child['source'])
            if nested != source and nested.is_relative_to(source):
                underlay = Path(parent['payload']) / nested.relative_to(source)
                if underlay != Path(child['payload']):
                    hidden.add(str(underlay))
    return sorted(hidden)


def literal_pattern(path):
    # Restic patterns use Go filepath.Match syntax, not shell quoting. Keep
    # absolute anchoring while escaping every glob metacharacter literally.
    return ''.join('\\' + char if char in '*?[]\\' else char for char in path)


def payload(settings, restic, directory, attempt, capture, *, identity):
    paths = []
    if capture is None:
        for app in settings['capture']['applications'].values():
            paths += [{'source': path, 'payload': path} for path in app['paths']]
    else:
        for item in capture['paths']:
            paths.append({'source': item['source'], 'payload': str(
                Path(capture['roots'][item['root']]['snapshot']) / item['relative'])})
    unique = {}
    for item in paths:
        if item['source'] in unique and unique[item['source']] != item['payload']:
            raise ValueError(_("Conflicting logical application paths in the captured backup."))
        unique[item['source']] = item['payload']
    paths = [{'source': source, 'payload': target} for source, target in sorted(unique.items())]
    recovery_metadata = recovery(settings)
    manifest = {'format': FORMAT, 'sourceId': settings['sourceId'], 'machineId': snapshots.machine_id(),
                'attemptId': attempt, 'createdAt': int(time.time()), 'captureMethod': settings['captureMethod'],
                'identity': identity, 'paths': paths, 'capture': None if capture is None else {k: v for k, v in capture.items() if k != 'applied'},
                'recovery': {'path': settings['recoveryFile'], 'sha256': recovery_metadata['archiveSha256'],
                             'systemPath': recovery_metadata['systemPath'], 'policySha256': recovery_metadata['policySha256']}}
    contents = encode(manifest)
    filename = directory / 'manifest.json'
    filename.write_text(contents)
    filename.chmod(0o600)
    arguments = ['backup', '--tag', 'nixberry-payload-v1', '--tag', 'source:' + settings['sourceId'],
                 '--tag', 'attempt:' + attempt]
    for hidden in hidden_payload_paths(paths):
        arguments += ['--exclude', literal_pattern(hidden)]
    arguments += ['--', str(filename), settings['recoveryFile']]
    arguments += sorted({item['payload'] for item in paths})
    identifier = completed(restic.run(arguments))
    if restic.run(['dump', identifier, str(filename)]) != contents:
        raise RuntimeError(_("The uploaded backup manifest does not match the capture."))
    archived_recovery = restic.run(['dump', identifier, settings['recoveryFile']], binary=True)
    if hashlib.sha256(archived_recovery).hexdigest() != manifest['recovery']['sha256']:
        raise RuntimeError(_("The uploaded recovery configuration does not match the capture."))
    return {'format': FORMAT, 'sourceId': settings['sourceId'], 'attemptId': attempt,
            'createdAt': manifest['createdAt'], 'payloadId': identifier,
            'manifestPath': str(filename), 'manifestSha256': hashlib.sha256(contents.encode()).hexdigest()}


def backup(value, *, apply=False):
    settings = policy(value)
    restic = Restic(settings)
    restic.repository()
    recovery(settings)
    capture_policy = settings['capture']
    topology = snapshots.inspect(capture_policy) if settings['captureMethod'] == 'snapshot' else snapshots.cold_identity(capture_policy)
    if not apply:
        return {'applied': False, 'captureMethod': settings['captureMethod'], 'sourceId': settings['sourceId'],
                'repository': settings['repository'], 'topology': topology}
    identity = backup_identity.collect()
    runtime = snapshots.secure_directory(settings['runtimeDirectory'], create=True)
    require_tmpfs(runtime)
    with snapshots.lock(capture_policy) as state, tempfile.TemporaryDirectory(dir=runtime) as temporary:
        # An update may have completed after the initial storage/recipe probes.
        # Validate again inside the lease before stopping any old writer set.
        recovery(settings)
        if snapshots.pending(capture_policy, state):
            raise RuntimeError(_("Interrupted capture needs writer recovery before another snapshot."))
        attempt = uuid.uuid4().hex
        directory = Path(temporary)
        capture = None
        if settings['captureMethod'] == 'snapshot':
            capture = snapshots.capture_locked(capture_policy, state, topology)
            receipt = payload(settings, restic, directory, attempt, capture, identity=identity)
        else:
            with snapshots.cold(capture_policy, state):
                receipt = payload(settings, restic, directory, attempt, None, identity=identity)
        recovery(settings)
        identifier = completed(restic.run(['backup', '--stdin', '--stdin-filename', 'nixberry-receipt.json',
            '--tag', RECEIPT, '--tag', 'source:' + settings['sourceId'], '--tag', 'attempt:' + attempt], input=encode(receipt)))
        # Release source blocks only after the encrypted payload and completion
        # receipt exist. The lock excludes explicit snapshot deletion throughout.
        if capture is not None:
            snapshots.remove_locked(capture_policy, state, capture['snapshotId'])
        return {'applied': True, 'snapshotId': identifier, 'payloadId': receipt['payloadId'],
                'sourceId': settings['sourceId'], 'state': 'recorded-captured'}


def inventory(value):
    return inventory_reader(Restic(policy(value)))


def inventory_reader(restic):
    settings = restic.settings
    restic.repository()
    rows = json.loads(restic.run(['snapshots', '--tag', RECEIPT + ',source:' + settings['sourceId']]))
    if not isinstance(rows, list):
        raise ValueError(_("Invalid Restic backup inventory."))
    result = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not ID.fullmatch(row['id'])
                or not tagged(row, settings['sourceId']) or not isinstance(row.get('time'), str)):
            raise ValueError(_("Invalid Restic backup inventory."))
        try:
            created = datetime.fromisoformat(row['time'].replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(_("Invalid Restic backup inventory.")) from None
        if created.utcoffset() is None:
            raise ValueError(_("Invalid Restic backup inventory."))
        result.append({'snapshotId': row['id'], 'createdAt': int(created.timestamp()), 'state': 'recorded-captured'})
    return {'snapshots': result}


def tagged(row, source):
    tags = row.get('tags')
    return (isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)
            and {RECEIPT, 'source:' + source}.issubset(tags))


def validate_manifest(record, manifest):
    fields = {'format', 'sourceId', 'machineId', 'attemptId', 'createdAt', 'captureMethod', 'paths', 'capture', 'recovery'}
    if isinstance(manifest, dict) and manifest.get('format') == FORMAT:
        fields.add('identity')
    if (not isinstance(manifest, dict) or set(manifest) != fields
            or manifest['format'] not in FORMATS or manifest['format'] != record['format']
            or manifest['sourceId'] != record['sourceId'] or manifest['attemptId'] != record['attemptId']
            or type(manifest['createdAt']) is not int or manifest['createdAt'] != record['createdAt']
            or not isinstance(manifest['machineId'], str)
            or not snapshots.ID.fullmatch(manifest['machineId']) or manifest['captureMethod'] not in ('snapshot', 'cold')
            or not isinstance(manifest['paths'], list) or not manifest['paths']
            or not isinstance(manifest['recovery'], dict) or set(manifest['recovery']) != {'path', 'sha256', 'systemPath', 'policySha256'}
            or not isinstance(manifest['recovery']['sha256'], str) or not ID.fullmatch(manifest['recovery']['sha256'])):
        raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))
    if manifest['format'] == FORMAT:
        backup_identity.validate(manifest['identity'])
    snapshots.absolute(manifest['recovery']['path'])
    if (not isinstance(manifest['recovery']['systemPath'], str) or not SYSTEM.fullmatch(manifest['recovery']['systemPath'])
            or not isinstance(manifest['recovery']['policySha256'], str) or not ID.fullmatch(manifest['recovery']['policySha256'])):
        raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))
    logical = {}
    for item in manifest['paths']:
        if not isinstance(item, dict) or set(item) != {'source', 'payload'}:
            raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))
        snapshots.absolute(item['source'])
        snapshots.absolute(item['payload'])
        if item['source'] in logical:
            raise ValueError(_("Conflicting logical application paths in the captured backup."))
        logical[item['source']] = item['payload']
    if manifest['captureMethod'] == 'cold':
        if manifest['capture'] is not None or any(source != target for source, target in logical.items()):
            raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))
    else:
        capture = manifest['capture']
        if not isinstance(capture, dict) or capture.get('state') != 'ready':
            raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))
        snapshots.validate_record(capture, capture.get('snapshotId'), expected_machine_id=manifest['machineId'])
        expected = {item['source']: str(Path(capture['roots'][item['root']]['snapshot']) / item['relative'])
                    for item in capture['paths']}
        if logical != expected:
            raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))


def receipt(value, identifier):
    return receipt_reader(Restic(policy(value)), identifier)


def receipt_reader(restic, identifier):
    settings = restic.settings
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError(_("Select a full Restic snapshot ID from the Restic backup inventory."))
    restic.repository()
    records = json.loads(restic.run(['snapshots', identifier]))
    if (not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict)
            or records[0].get('id') != identifier or not tagged(records[0], settings['sourceId'])):
        raise ValueError(_("The selected Restic snapshot is not a completion receipt for this source."))
    record = json.loads(restic.run(['dump', identifier, '/nixberry-receipt.json']))
    required = {'format', 'sourceId', 'attemptId', 'createdAt', 'payloadId', 'manifestPath', 'manifestSha256'}
    if (not isinstance(record, dict) or set(record) != required or record['format'] not in FORMATS
            or record['sourceId'] != settings['sourceId'] or not isinstance(record['payloadId'], str)
            or not ID.fullmatch(record['payloadId']) or not isinstance(record['manifestSha256'], str)
            or not ID.fullmatch(record['manifestSha256']) or type(record['createdAt']) is not int
            or record['createdAt'] < 0 or not isinstance(record['attemptId'], str)
            or not snapshots.ID.fullmatch(record['attemptId'])):
        raise ValueError(_("Invalid Restic completion receipt."))
    snapshots.absolute(record['manifestPath'])
    contents = restic.run(['dump', record['payloadId'], record['manifestPath']])
    if hashlib.sha256(contents.encode()).hexdigest() != record['manifestSha256']:
        raise ValueError(_("The encrypted capture manifest does not match its completion receipt."))
    manifest = json.loads(contents)
    validate_manifest(record, manifest)
    return restic, record, manifest


def verify(value, identifier):
    return verify_reader(Restic(policy(value)), identifier)


def verify_reader(restic, identifier):
    restic, record, manifest = receipt_reader(restic, identifier)
    # Restic authenticates all payload files while streaming the archive to /dev/null.
    restic.run(['dump', record['payloadId'], '/'], discard=True)
    recipe_reader(restic, record, manifest)
    return {'snapshotId': identifier, 'payloadId': record['payloadId'], 'state': 'recorded-verified'}


def recipe_reader(restic, record, manifest):
    archive = restic.run(['dump', record['payloadId'], manifest['recovery']['path']], binary=True,
                         max_output=recovery_recipe.MAX_ARCHIVE)
    if hashlib.sha256(archive).hexdigest() != manifest['recovery']['sha256']:
        raise ValueError(_("The uploaded recovery configuration does not match the capture."))
    recipe = recovery_recipe.validate(archive)
    if (recipe['sourceId'] != restic.settings['sourceId']
            or 'target' in restic.settings and recipe['target'] != restic.settings['target']
            or recipe['systemPath'] != manifest['recovery']['systemPath']
            or recipe['policySha256'] != manifest['recovery']['policySha256']):
        raise ValueError(_("The sealed recovery recipe does not match this source and capture."))
    return archive, recipe


def main(argv=None):
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument('action', choices=['backup-init', 'backup-stage', 'backup', 'backups', 'verify-backup',
                                         'backup-prune', 'backup-check'])
    parser.add_argument('--config', default='/etc/nixberry/backup.json', help=_("Configuration-owned Restic policy file"))
    parser.add_argument('--maintenance', default='/etc/nixberry/maintenance.json', help=_("Configuration-owned backup maintenance policy file"))
    parser.add_argument('--snapshot', help=_("Full Restic completion snapshot ID"))
    parser.add_argument('--system', help=_("Expected running NixOS generation for recovery staging or retention"))
    parser.add_argument('--receive-sha256', help=_("Receive a sealed recipe archive from standard input with this SHA256 identity"))
    parser.add_argument('--json', action='store_true', help=_("Print technical backup metadata as JSON"))
    parser.add_argument('--statistics', action='store_true',
                        help=_("Include completed restore-point count and latest logical backup size"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true', help=_("Apply Restic application backup"))
    modes.add_argument('--dry-run', action='store_true', help=_("Inspect backup readiness without stopping writers"))
    args = parser.parse_args(argv)
    if args.statistics and args.action != 'backup-check':
        parser.error(_("--statistics only applies to Restic backup freshness checks."))
    if os.geteuid() != 0:
        raise RuntimeError(_("Restic backup operations require root and protected runtime credentials."))
    if args.apply and args.action not in ('backup-init', 'backup-stage', 'backup', 'backup-prune'):
        parser.error(_("--apply only applies to Restic backup capture, staging, retention or repository initialization."))
    if args.snapshot is not None and args.action != 'verify-backup':
        parser.error(_("--snapshot only applies to Restic backup verification."))
    if args.dry_run and args.action not in ('backup-init', 'backup-stage', 'backup', 'backup-prune', 'backup-check'):
        parser.error(_("--dry-run only applies to Restic backup capture, staging, maintenance or repository initialization."))
    if args.system is not None and args.action not in ('backup-stage', 'backup-prune'):
        parser.error(_("--system only applies to recovery archive staging or Restic retention."))
    if args.receive_sha256 is not None and args.action != 'backup-stage':
        parser.error(_("--receive-sha256 only applies to recovery archive staging."))
    def interrupted(signum, frame):
        raise InterruptedError(_("Restic backup was interrupted; checking writer recovery."))
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    expected_system = args.system or (running_system() if args.action == 'backup-prune' else None)
    settings = policy(json.loads(Path(args.config).read_text()))
    if args.action == 'backup-init':
        result = initialize(settings, apply=args.apply)
    elif args.action == 'backup-stage':
        if args.receive_sha256 is not None and not ID.fullmatch(args.receive_sha256):
            parser.error(_("Recovery transfer requires a full SHA256 archive identity."))
        archive = read_archive(sys.stdin.buffer) if args.apply and args.receive_sha256 is not None else None
        result = stage(settings, args.system, archive=archive, expected_sha256=args.receive_sha256, apply=args.apply)
    elif args.action == 'backup':
        result = backup(settings, apply=args.apply)
    elif args.action == 'backups':
        result = inventory(settings)
    elif args.action in ('backup-check', 'backup-prune'):
        import restic_maintenance
        maintenance = restic_maintenance.policy(json.loads(Path(args.maintenance).read_text()))
        if args.dry_run:
            result = {'action': args.action, 'maintenance': maintenance, 'applied': False, 'readiness': 'not-checked'}
        elif args.action == 'backup-check':
            result = restic_maintenance.check(settings, maintenance, include_statistics=args.statistics)
        else:
            result = restic_maintenance.prune(settings, maintenance, expected_system, apply=args.apply)
    else:
        result = verify(settings, args.snapshot)
    print(json.dumps(result, sort_keys=True))
    return int(result.get('status') != 'ok') if args.action == 'backup-check' and not args.dry_run else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
        error("{error}", error=str(failure))
        raise SystemExit(1)
