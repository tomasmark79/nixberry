"""Restore authenticated application payloads into new private staging only."""
import errno
import os
from pathlib import Path
import stat
import struct

import backup_identity
from messages import _
import restic_backup
import snapshots


def ownership(source, target):
    source = backup_identity.validate(source)
    target = backup_identity.validate(target)
    if source['architecture'] != target['architecture']:
        raise ValueError(_("Source and destination architectures must match for application restoration."))
    tables = {}
    for field, kind in (('uid', 'users'), ('gid', 'groups')):
        tables[field] = []
        for evidence in (source, target):
            by_id, by_name = {}, {}
            for row in evidence[kind]:
                by_id.setdefault(row[field], set()).add(row['name'])
                by_name.setdefault(row['name'], set()).add(row[field])
            tables[field].append((by_id, by_name))

    def check(field, value):
        if field not in tables or type(value) is not int or not 0 <= value < 2 ** 32 - 1:
            raise ValueError(_("Invalid numeric ownership in restored application data."))
        (source_ids, source_names), (target_ids, target_names) = tables[field]
        names = source_ids.get(value, set())
        if (names != target_ids.get(value, set()) or any(
                source_names[name] != {value} or target_names[name] != {value} for name in names)):
            raise ValueError(_("A restored numeric owner differs between source and destination accounts."))
    return check


def acl_owners(contents, check):
    if len(contents) < 4 or (len(contents) - 4) % 8 or struct.unpack_from('<I', contents)[0] != 2:
        raise ValueError(_("Invalid POSIX ACL in restored application data."))
    for offset in range(4, len(contents), 8):
        tag, mode, owner = struct.unpack_from('<HHI', contents, offset)
        if mode > 7 or tag not in (1, 2, 4, 8, 16, 32):
            raise ValueError(_("Invalid POSIX ACL in restored application data."))
        if tag in (2, 8):
            check('uid' if tag == 2 else 'gid', owner)
        elif owner != 2 ** 32 - 1:
            raise ValueError(_("Invalid POSIX ACL in restored application data."))


def capability_owners(contents, check):
    if len(contents) < 4:
        raise ValueError(_("Invalid file capabilities in restored application data."))
    revision = struct.unpack_from('<I', contents)[0] >> 24
    if (revision, len(contents)) not in ((1, 12), (2, 20), (3, 24)):
        raise ValueError(_("Invalid file capabilities in restored application data."))
    check('uid', struct.unpack_from('<I', contents, 20)[0] if revision == 3 else 0)


def inspect_tree(root, check):
    pending = [Path(root)]
    while pending:
        path = pending.pop()
        details = path.lstat()
        check('uid', details.st_uid)
        check('gid', details.st_gid)
        for attribute in ('system.posix_acl_access', 'system.posix_acl_default', 'security.capability'):
            try:
                contents = os.getxattr(path, attribute, follow_symlinks=False)
            except OSError as failure:
                if failure.errno in (errno.ENODATA, errno.ENOTSUP):
                    continue
                raise
            if attribute == 'security.capability':
                capability_owners(contents, check)
            else:
                acl_owners(contents, check)
        if stat.S_ISDIR(details.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)


def stage(reader, identifier, destination, target_identity):
    if os.geteuid() != 0:
        raise ValueError(_("Application payload restoration requires root to preserve ownership and metadata."))
    # Completion receipts authenticate both the logical mapping and evidence.
    reader, receipt, manifest = restic_backup.receipt_reader(reader, identifier)
    if manifest['format'] != restic_backup.FORMAT:
        raise ValueError(_("Application restoration requires source architecture and ownership evidence."))
    check = ownership(manifest['identity'], target_identity)
    restic_backup.recipe_reader(reader, receipt, manifest)
    destination = Path(destination)
    snapshots.absolute(str(destination))
    snapshots.secure_directory(destination.parent, private=False, owner_uid=reader.owner_uid)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(_("Payload restoration requires a new private staging directory."))
    destination.mkdir(mode=0o700)
    incomplete = destination / 'INCOMPLETE'
    incomplete.write_text('Application restoration has not completed.\n')
    incomplete.chmod(0o600)
    data = destination / 'data'
    data.mkdir(mode=0o700)
    paths = manifest['paths']
    reader.restore(receipt['payloadId'], data, paths)
    rows = []
    for item in paths:
        restored = data / item['payload'].removeprefix('/')
        # The selected logical roots must exist and cannot traverse staged links.
        if restored.parent.resolve(strict=True) != restored.parent:
            raise ValueError(_("Restored application roots must not traverse symbolic links."))
        inspect_tree(restored, check)
        rows.append({'source': item['source'], 'staged': str(restored)})
    report = {'version': 1, 'state': 'staged-verified', 'sourceId': receipt['sourceId'],
              'snapshotId': identifier, 'payloadId': receipt['payloadId'],
              'manifestSha256': receipt['manifestSha256'], 'sourceMachineId': manifest['machineId'],
              'architecture': target_identity['architecture'], 'paths': rows}
    snapshots.write_record(destination / 'stage.json', report)
    incomplete.unlink()
    # This is a verified private stage, never a live restore completion marker.
    return report
