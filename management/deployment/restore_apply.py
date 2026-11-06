"""Publish an authenticated restore transaction while destination writers are held."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import time
import uuid

import backup_identity
from messages import _
import restore_payload
import restore_target
import snapshots

PENDING = 'restore-pending.json'


def roots(value):
    paths = sorted({path for app in value['applications'].values() for path in app['paths']})
    return [Path(path) for path in paths if not any(Path(path).is_relative_to(other) for other in paths if other != path)]


def protected_object(path):
    """Treat any symlink on the route as protected without following it."""
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = path.parts[1:]
        for index, name in enumerate(parts):
            try:
                details = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(details.st_mode) or index == len(parts) - 1:
                return True
            if not stat.S_ISDIR(details.st_mode):
                return False
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def protect(paths, state, view=None):
    reserved = ['/bin', '/sbin', '/lib', '/lib64', '/usr', '/boot', '/dev', '/proc', '/sys', '/run', '/nix',
                '/etc/passwd', '/etc/group', '/etc/shadow', '/etc/gshadow', '/etc/machine-id', '/etc/ssh',
                '/etc/nixos', '/var/lib/nixos', '/var/lib/systemd', '/root/.ssh', state]
    ssh_paths = {Path(user.pw_dir) / '.ssh' for user in pwd.getpwall() if user.pw_dir.startswith('/')}
    for name in paths:
        snapshots.absolute(str(name))
        if any(Path(name).is_relative_to(item) or Path(item).is_relative_to(name) for item in reserved):
            raise ValueError(_("Application restoration must not replace operating-system identity or restore state."))
        for ssh in ssh_paths:
            if Path(name).is_relative_to(ssh) or (ssh.is_relative_to(name) and (
                    protected_object(ssh) or view is not None and protected_object(view / str(ssh).removeprefix('/')))):
                raise ValueError(_("Application restoration must not replace operating-system identity or restore state."))


def layout(paths):
    rows = json.loads(snapshots.run(['findmnt', '--json', '--list', '--output', 'TARGET,FSTYPE,UUID,FSROOT,OPTIONS']))['filesystems']
    selected = {}
    for path in paths:
        ancestors = [row for row in rows if Path(path).is_relative_to(row['target'])]
        if not ancestors:
            raise ValueError(_("Cannot identify the destination filesystem layout."))
        covering = max(ancestors, key=lambda row: len(Path(row['target']).parts))
        for row in [covering, *(item for item in rows if Path(item['target']).is_relative_to(path))]:
            if (row['fstype'] not in ('btrfs', 'ext4') or not isinstance(row.get('uuid'), str)
                    or not snapshots.UUID.fullmatch(row['uuid']) or 'ro' in row.get('options', '').split(',')):
                raise ValueError(_("Restore destinations require identified writable Btrfs or ext4 filesystems."))
            selected[row['target']] = {key: row[key] for key in ('target', 'fstype', 'uuid', 'fsroot')}
    return [selected[key] for key in sorted(selected)]


def walk(root):
    yield root
    if root.is_dir() and not root.is_symlink():
        with os.scandir(root) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                yield from walk(Path(entry.path))


def empty(paths, view=None):
    for root in paths:
        if root.is_symlink() or root.parent.resolve() != root.parent:
            raise ValueError(_("Destination application paths must not traverse symbolic links."))
        if root.exists():
            for path in walk(root):
                if not path.is_dir() or path.is_symlink():
                    raise ValueError(_("A new restore requires empty destination application paths."))
                if view is not None and not (view / str(path).removeprefix('/')).is_dir():
                    raise ValueError(_("Destination scaffolding is absent from the selected application backup."))


def canonical(report, directory):
    view = directory / 'view'
    if view.exists():
        shutil.rmtree(view)
    view.mkdir(mode=0o700)
    mappings = sorted(report['paths'], key=lambda item: len(Path(item['source']).parts))
    selected = []
    for item in mappings:
        logical, staged = Path(item['source']), Path(item['staged'])
        snapshots.absolute(str(logical))
        if not staged.is_relative_to(directory / 'data') or staged.parent.resolve() != staged.parent:
            raise ValueError(_("The private restore stage contains an invalid application mapping."))
        if any(logical.is_relative_to(previous['source']) and staged == Path(previous['staged']) / logical.relative_to(previous['source'])
               for previous in selected):
            continue
        target = view / str(logical).removeprefix('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshots.run(['cp', '--archive', '--link', '--no-target-directory', '--', str(staged), str(target)])
        selected.append(item)
    # Adding separately captured children changes parent directory timestamps.
    for path in sorted((path for path in walk(view) if path.is_dir() and not path.is_symlink()),
                       key=lambda path: len(path.parts), reverse=True):
        logical = Path('/') / path.relative_to(view)
        covering = [item for item in mappings if logical.is_relative_to(item['source'])]
        if covering:
            item = max(covering, key=lambda item: len(Path(item['source']).parts))
            original = Path(item['staged']) / logical.relative_to(item['source'])
            shutil.copystat(original, path, follow_symlinks=False)
    return view


def tree_hash(base, paths, *, durable=False):
    digest = hashlib.sha256()
    links = {}
    for logical in paths:
        root = base / str(logical).removeprefix('/')
        for path in walk(root):
            details = path.lstat()
            relative = str(logical / path.relative_to(root))
            attributes = {key: os.getxattr(path, key, follow_symlinks=False).hex()
                          for key in sorted(os.listxattr(path, follow_symlinks=False))}
            row = [relative, details.st_uid, details.st_gid, details.st_mode, details.st_mtime_ns, attributes]
            if stat.S_ISREG(details.st_mode):
                token = (details.st_dev, details.st_ino)
                row.append(links.setdefault(token, relative))
                contents = hashlib.sha256()
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, 'rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        contents.update(chunk)
                    if durable:
                        os.fsync(stream.fileno())
                row += [details.st_size, contents.hexdigest()]
            elif stat.S_ISLNK(details.st_mode):
                row.append(os.readlink(path))
            elif stat.S_ISCHR(details.st_mode) and details.st_rdev == os.makedev(0, 0):
                # Overlay whiteouts have device metadata, never file contents.
                row.append(details.st_rdev)
            elif not stat.S_ISDIR(details.st_mode) and not stat.S_ISFIFO(details.st_mode):
                raise ValueError(_("Unsupported special file in application restoration."))
            digest.update(json.dumps(row, ensure_ascii=True, separators=(',', ':')).encode() + b'\n')
        if durable:
            for path in reversed(list(walk(root))):
                if path.is_dir() and not path.is_symlink():
                    snapshots.sync_directory(path)
            snapshots.sync_directory(root.parent)
    return digest.hexdigest()


def compatible_links(view, paths):
    groups = {}
    for logical in paths:
        root = view / str(logical).removeprefix('/')
        for path in walk(root):
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_nlink < 2:
                continue
            target = logical / path.relative_to(root)
            ancestor = target.parent
            while not ancestor.exists():
                ancestor = ancestor.parent
            device = ancestor.stat().st_dev
            token = (details.st_dev, details.st_ino)
            if groups.setdefault(token, device) != device:
                raise ValueError(_("Destination filesystems cannot preserve a source hardlink group."))


def link_dest_compatible(view, paths):
    mounts = list(map(Path, snapshots.mountpoints()))
    def covering(path):
        return max((mount for mount in mounts if path.is_relative_to(mount)), key=lambda mount: len(mount.parts))
    source_mount = covering(view)
    device = view.stat().st_dev
    for path in paths:
        if covering(path) != source_mount or any(mount != source_mount and mount.is_relative_to(path) for mount in mounts):
            return False
        ancestor = path
        while not ancestor.exists() and not ancestor.is_symlink():
            ancestor = ancestor.parent
        if ancestor.lstat().st_dev != device:
            return False
        pending = [path]
        while pending:
            destination = pending.pop()
            try:
                details = destination.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(details.st_mode):
                continue
            # Btrfs subvolumes can change devices without a mountpoint.
            if details.st_dev != device:
                return False
            with os.scandir(destination) as entries:
                pending.extend(Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False))
    return True


def transfer(view, paths, directory, *, check=False):
    listing = directory / 'paths.list'
    listing.write_bytes(b''.join(os.fsencode(str(path).removeprefix('/')) + b'\0' for path in paths))
    temporary = directory / 'temporary'
    temporary.mkdir(mode=0o700, exist_ok=True)
    arguments = ['rsync', '--archive', '--hard-links', '--acls', '--xattrs', '--sparse', '--numeric-ids',
                 '--recursive', '--relative', '--no-implied-dirs', '--checksum', '--from0', '--files-from=' + str(listing)]
    if check:
        arguments += ['--dry-run', '--delete', '--itemize-changes']
    else:
        if link_dest_compatible(view, paths):
            arguments += ['--link-dest=' + str(view)]
        arguments += ['--temp-dir=' + str(temporary)]
    result = snapshots.run([*arguments, '--', str(view) + '/', '/'], timeout=86400)
    if check and result:
        raise ValueError(_("Restored application data differs or contains unexpected files; no completion was written."))
    if not check:
        # Restore directory and link metadata explicitly below declared roots.
        # --no-implied-dirs must never alter the live ancestors of those roots.
        for logical in paths:
            root = view / str(logical).removeprefix('/')
            for source in walk(root):
                details = source.lstat()
                if not stat.S_ISDIR(details.st_mode) and not stat.S_ISLNK(details.st_mode):
                    continue
                target = logical / source.relative_to(root)
                os.chown(target, details.st_uid, details.st_gid, follow_symlinks=False)
                attributes = set(os.listxattr(source, follow_symlinks=False))
                for attribute in set(os.listxattr(target, follow_symlinks=False)) - attributes:
                    os.removexattr(target, attribute, follow_symlinks=False)
                shutil.copystat(source, target, follow_symlinks=False)


def start_units(value):
    units = sorted({unit for app in value['applications'].values() for unit in app['units']})
    if units:
        snapshots.run(['systemctl', 'start', *units])


def guard(value, expected_system, expected_machine):
    if (snapshots.running_system() != expected_system or snapshots.machine_id() != expected_machine
            or not restore_target.SYSTEM.fullmatch(expected_system) or not snapshots.ID.fullmatch(expected_machine)):
        raise ValueError(_("The destination machine or generation differs from the explicit restore target."))
    protect(roots(value), value['stateDirectory'])
    for path in roots(value):
        snapshots.secure_directory(path.parent, private=False)
    report = restore_target.status(value, expected_system=expected_system, inspect_paths=False)
    if report['completed'] or any(state not in ('inactive', 'failed') for state in report['units'].values()):
        raise ValueError(_("Destination writers must remain held until application restoration completes."))


def restore(value, capture, reader, identifier, expected_system, expected_machine, *, apply=False):
    if os.geteuid() != 0:
        raise ValueError(_("Application payload restoration requires root to preserve ownership and metadata."))
    value = restore_target.policy(value)
    capture = snapshots.policy(capture)
    if (not isinstance(identifier, str) or not restore_payload.native_restic.ID.fullmatch(identifier)
            or snapshots.running_system() != expected_system or snapshots.machine_id() != expected_machine):
        raise ValueError(_("The destination machine or generation differs from the explicit restore target."))
    paths = roots(value)
    protect(paths, value['stateDirectory'])
    internal = [reader.settings.get(key) for key in ('passwordFile', 'runtimeDirectory')]
    if reader.settings.get('ssh'):
        internal += [reader.settings['ssh'][key] for key in ('identityFile', 'knownHostsFile')]
    if str(reader.settings.get('repository', '')).startswith('/'):
        internal.append(reader.settings['repository'])
    if any(Path(item).resolve().is_relative_to(path) or path.is_relative_to(Path(item).resolve())
           for item in internal if item for path in paths):
        raise ValueError(_("Restore access credentials and repository storage must stay outside application data."))
    if {path for app in value['applications'].values() for path in app['paths']} != {
            path for app in capture['applications'].values() for path in app['paths']}:
        raise ValueError(_("Installed capture and restore application catalogues differ."))
    binding = {'machineId': expected_machine, 'systemPath': expected_system,
               'policySha256': restore_target.policy_hash(value), 'sourceId': reader.settings['sourceId'], 'snapshotId': identifier}
    marker = restore_target.marker_path(value)
    if restore_target.completed(value):
        record = snapshots.read_record(marker)
        if any(record[key] != binding[key] for key in ('machineId', 'policySha256', 'sourceId', 'snapshotId')):
            raise ValueError(_("This destination already contains a different completed restoration."))
        if apply:
            with contextlib.ExitStack() as stack:
                for directory in sorted({capture['stateDirectory'], value['stateDirectory']}):
                    stack.enter_context(snapshots.lock({'stateDirectory': directory}))
                if snapshots.running_system() != expected_system or snapshots.machine_id() != expected_machine:
                    raise ValueError(_("The destination machine or generation differs from the explicit restore target."))
                leftover = Path(value['stateDirectory']) / PENDING
                if leftover.exists() or leftover.is_symlink():
                    interrupted = snapshots.read_record(leftover)
                    if any(interrupted.get(key) != item for key, item in binding.items()):
                        raise ValueError(_("The pending restore belongs to a different source, destination or filesystem layout."))
                    leftover.unlink()
                    snapshots.sync_directory(leftover.parent)
                start_units(value)
        return {**binding, 'applied': apply, 'state': 'recorded-restored'}
    guard(value, expected_system, expected_machine)
    pending_file = Path(value['stateDirectory']) / PENDING
    current_layout = layout([*paths, Path(value['stateDirectory'])])
    if not pending_file.exists() and not pending_file.is_symlink():
        empty(paths)
    if not apply:
        return {**binding, 'applied': False, 'state': 'planned', 'pending': pending_file.exists()}
    with contextlib.ExitStack() as stack:
        for directory in sorted({capture['stateDirectory'], value['stateDirectory']}):
            stack.enter_context(snapshots.lock({'stateDirectory': directory}))
        guard(value, expected_system, expected_machine)
        if snapshots.pending(capture, Path(capture['stateDirectory'])):
            raise ValueError(_("Recover pending capture writers before restoring application data."))
        if pending_file.exists() or pending_file.is_symlink():
            pending = snapshots.read_record(pending_file)
            required = set(binding) | {'version', 'phase', 'layout', 'directory'}
            if pending.get('phase') != 'staging':
                required.add('treeSha256')
            if (set(pending) != required or type(pending.get('version')) is not int or pending['version'] != 1
                    or any(pending.get(key) != item for key, item in binding.items())
                    or pending.get('layout') != current_layout or pending.get('phase') not in ('staging', 'copying', 'published')
                    or not isinstance(pending.get('directory'), str)):
                raise ValueError(_("The pending restore belongs to a different source, destination or filesystem layout."))
        else:
            empty(paths)
            pending = {**binding, 'version': 1, 'phase': 'staging', 'layout': current_layout,
                       'directory': str(pending_file.parent / ('restore-' + uuid.uuid4().hex))}
            snapshots.write_record(pending_file, pending)
        directory = Path(pending['directory'])
        if directory.parent != pending_file.parent or not directory.name.startswith('restore-') or not snapshots.ID.fullmatch(directory.name[8:]):
            raise ValueError(_("Invalid private restore transaction directory."))
        if directory.exists() or directory.is_symlink():
            snapshots.secure_directory(directory)
        if pending['phase'] == 'staging':
            # Only the copying journal binds a fully synced, verified view.
            # Any earlier private stage is rebuilt after interruption, even
            # when its report was written before the durable phase transition.
            if directory.exists():
                snapshots.secure_directory(directory)
                shutil.rmtree(directory)
            report = restore_payload.stage(reader, identifier, directory, backup_identity.collect())
            names = {row['source'] for row in report['paths']}
            declared = {path for app in value['applications'].values() for path in app['paths']}
            if (report['architecture'] != value['architecture'] or report['sourceMachineId'] == expected_machine or report['snapshotId'] != identifier
                    or report['sourceId'] != binding['sourceId'] or not declared.issubset(names)
                    or any(not any(Path(name).is_relative_to(root) for root in paths) for name in names)):
                raise ValueError(_("The selected backup does not match the distinct destination application catalogue."))
            view = canonical(report, directory)
            protect(paths, value['stateDirectory'], view)
            empty(paths, view)
            compatible_links(view, paths)
            pending.update(phase='copying', treeSha256=tree_hash(view, paths, durable=True))
            snapshots.write_record(pending_file, pending)
        if pending['phase'] == 'copying':
            view = directory / 'view'
            if tree_hash(view, paths) != pending['treeSha256']:
                raise ValueError(_("The private verified application stage changed during restoration."))
            protect(paths, value['stateDirectory'], view)
            guard(value, expected_system, expected_machine)
            if layout([*paths, Path(value['stateDirectory'])]) != pending['layout']:
                raise ValueError(_("The destination filesystem layout changed during restoration."))
            for path in paths:
                source = view / str(path).removeprefix('/')
                if source.is_dir() and not source.is_symlink() and path.is_symlink():
                    raise ValueError(_("Destination application paths must not traverse symbolic links."))
            transfer(view, paths, directory)
            transfer(view, paths, directory, check=True)
            if tree_hash(Path('/'), paths, durable=True) != pending['treeSha256']:
                raise ValueError(_("Restored application data differs or contains unexpected files; no completion was written."))
            pending['phase'] = 'published'
            snapshots.write_record(pending_file, pending)
        guard(value, expected_system, expected_machine)
        if layout([*paths, Path(value['stateDirectory'])]) != pending['layout']:
            raise ValueError(_("The destination filesystem layout changed during restoration."))
        if tree_hash(Path('/'), paths, durable=True) != pending['treeSha256']:
            raise ValueError(_("Restored application data changed before restore completion."))
        if directory.exists():
            snapshots.secure_directory(directory)
            shutil.rmtree(directory)
        snapshots.write_record(marker, {'version': 1, **binding, 'completedAt': int(time.time())})
        pending_file.unlink()
        snapshots.sync_directory(pending_file.parent)
        start_units(value)
        return {**binding, 'applied': True, 'state': 'recorded-restored'}


def abort(value, capture, expected_system, expected_machine, *, apply=False):
    if os.geteuid() != 0:
        raise ValueError(_("Application payload restoration requires root to preserve ownership and metadata."))
    value = restore_target.policy(value)
    capture = snapshots.policy(capture)
    guard(value, expected_system, expected_machine)
    pending_file = Path(value['stateDirectory']) / PENDING
    if not pending_file.exists() and not pending_file.is_symlink():
        raise ValueError(_("No unpublished restore transaction exists to abort."))
    with contextlib.ExitStack() as stack:
        if apply:
            for directory in sorted({capture['stateDirectory'], value['stateDirectory']}):
                stack.enter_context(snapshots.lock({'stateDirectory': directory}))
        guard(value, expected_system, expected_machine)
        pending = snapshots.read_record(pending_file)
        if (pending.get('phase') != 'staging' or pending.get('machineId') != expected_machine
                or pending.get('systemPath') != expected_system or pending.get('policySha256') != restore_target.policy_hash(value)):
            raise ValueError(_("Only an unpublished staging transaction can be aborted; partial application data is never deleted."))
        empty(roots(value))
        directory = Path(pending['directory'])
        if directory.parent != pending_file.parent or not directory.name.startswith('restore-') or not snapshots.ID.fullmatch(directory.name[8:]):
            raise ValueError(_("Invalid private restore transaction directory."))
        if apply:
            if directory.exists() or directory.is_symlink():
                snapshots.secure_directory(directory)
                shutil.rmtree(directory)
            pending_file.unlink()
            snapshots.sync_directory(pending_file.parent)
        return {'applied': apply, 'state': 'aborted-unpublished' if apply else 'planned-abort'}
