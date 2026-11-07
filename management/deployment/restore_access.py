"""Transfer bounded Restic restore credentials through SSH stdin into private tmpfs."""
import base64
import contextlib
import json
import os
from pathlib import Path
import select
import tempfile
import time

from messages import _
import restic_access
import restic_backup
import snapshots

LIMIT = 5 * 1024 * 1024


def pack(value):
    names = {'password': value['passwordFile']}
    if value['ssh'] is not None:
        names.update(identity=value['ssh']['identityFile'], knownHosts=value['ssh']['knownHostsFile'])
    files = {name: base64.b64encode(restic_backup.private_file(path, owner_uid=os.geteuid(),
             private=name != 'knownHosts', limit=1024 * 1024)).decode() for name, path in names.items()}
    context = {key: value[key] for key in ('target', 'sourceId', 'repository', 'repositoryId', 'timeoutSeconds')}
    context.update(version=1, ssh=None if value['ssh'] is None else {
        key: value['ssh'][key] for key in ('port', 'warnNoPqKex') if key in value['ssh']})
    contents = json.dumps({'version': 1, 'context': context, 'files': files}).encode()
    if len(contents) > LIMIT:
        raise ValueError(_("Restore access transfer exceeds its bounded credential size."))
    return contents


def read(stream):
    deadline = time.monotonic() + 30
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream.fileno()], [], [], remaining)[0]:
            raise TimeoutError(_("Restore access transfer timed out."))
        chunk = os.read(stream.fileno(), 65536)
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > LIMIT:
            raise ValueError(_("Restore access transfer exceeds its bounded credential size."))


@contextlib.contextmanager
def receive(contents, source):
    if len(contents) > LIMIT:
        raise ValueError(_("Restore access transfer exceeds its bounded credential size."))
    request = json.loads(contents)
    if (not isinstance(request, dict) or set(request) != {'version', 'context', 'files'}
            or type(request['version']) is not int or request['version'] != 1
            or not isinstance(request['context'], dict) or set(request['context']) != {
                'version', 'target', 'sourceId', 'repository', 'repositoryId', 'timeoutSeconds', 'ssh'}
            or not isinstance(request['files'], dict)):
        raise ValueError(_("Invalid Restic restore access transfer."))
    value = dict(request['context'])
    ssh = value['ssh']
    names = {'password'} if ssh is None else {'password', 'identity', 'knownHosts'}
    if set(request['files']) != names or ssh is not None and (
            not isinstance(ssh, dict) or set(ssh) not in ({'port'}, {'port', 'warnNoPqKex'})
            or type(ssh.get('warnNoPqKex', True)) is not bool):
        raise ValueError(_("Invalid Restic restore access transfer."))
    files = {}
    for name, encoded in request['files'].items():
        if not isinstance(encoded, str):
            raise ValueError(_("Invalid Restic restore access transfer."))
        files[name] = base64.b64decode(encoded, validate=True)
        if len(files[name]) > 1024 * 1024:
            raise ValueError(_("Restore access transfer exceeds its bounded credential size."))
    parent = snapshots.secure_directory('/run/nixberry-restore-access', create=True)
    restic_backup.require_tmpfs(parent)
    with tempfile.TemporaryDirectory(prefix='request-', dir=parent) as temporary:
        directory = Path(temporary)
        for name, data in files.items():
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(data)
        value.update(passwordFile=str(directory / 'password'), runtimeDirectory=str(directory / 'runtime'))
        if ssh is not None:
            value['ssh'] = {**ssh, 'identityFile': str(directory / 'identity'), 'knownHostsFile': str(directory / 'knownHosts')}
        context = directory / 'access.json'
        context.write_text(json.dumps(value))
        context.chmod(0o600)
        value = restic_access.load(context, source)
        restic_access.runtime(value)
        yield value
