"""Read Restic encrypted backups using independent, private storage access."""
import json
import os
from pathlib import Path
import sys

from messages import _
import restic_health
import restic_backup
import recovery_recipe
import snapshots

ACTIONS = ('backups', 'backup-check', 'verify-backup', 'recover-source')


def load(path, target):
    path = Path(path).expanduser().absolute()
    restic_backup.runtime_path(str(path))
    value = json.loads(restic_backup.private_file(path, owner_uid=os.geteuid(), limit=1024 * 1024))
    required = {'version', 'target', 'sourceId', 'repository', 'repositoryId', 'passwordFile',
                'ssh', 'runtimeDirectory', 'timeoutSeconds'}
    if (not isinstance(value, dict) or set(value) != required or type(value['version']) is not int
            or value['version'] != 1 or value['target'] != target
            or not isinstance(target, str) or not recovery_recipe.NAME.fullmatch(target)
            or not isinstance(value['repositoryId'], str) or not restic_backup.ID.fullmatch(value['repositoryId'])):
        raise ValueError(_("Invalid Restic backup access context or source identity."))
    restic_backup.storage_policy(value)
    paths = [str(path), value['passwordFile'], value['runtimeDirectory']]
    if value['ssh'] is not None:
        paths += [value['ssh']['identityFile'], value['ssh']['knownHostsFile']]
    recipe = os.environ.get('NIXBERRY_RECIPE')
    if recipe and any(Path(item).resolve().is_relative_to(Path(recipe).resolve()) for item in paths):
        raise ValueError(_("Keep backup access contexts and runtime credentials outside the recipe."))
    return value


def runtime(value):
    directory = snapshots.secure_directory(value['runtimeDirectory'], create=True, owner_uid=os.geteuid())
    restic_backup.require_tmpfs(directory)


def output_path(value):
    destination = Path(os.path.abspath(Path(value).expanduser()))
    restic_backup.runtime_path(str(destination))
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(_("Recovery requires a new output directory; existing data is never overwritten."))
    snapshots.secure_directory(destination.parent, private=False, owner_uid=os.geteuid())
    recipe = os.environ.get('NIXBERRY_RECIPE')
    if recipe and destination.is_relative_to(Path(recipe).resolve()):
        raise ValueError(_("Choose a recovery output directory outside the recipe."))
    return destination


def run(path, target, action, *, snapshot=None, output=None, apply=False, dry_run=False, max_age_seconds=None):
    if sys.platform != 'linux':
        raise RuntimeError(_("Restic backup access currently requires Linux."))
    if action not in ACTIONS:
        raise ValueError(_("This operation does not support independent Restic backup access."))
    if apply and action != 'recover-source':
        raise ValueError(_("Independent backup reads do not accept --apply."))
    if action == 'backup-check':
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= restic_health.MAX_AGE_SECONDS:
            raise ValueError(_("Backup freshness requires a positive age limit of at most one year in seconds."))
    elif max_age_seconds is not None:
        raise ValueError(_("An age limit is supported only for independent backup freshness checks."))
    if action in ('backups', 'backup-check'):
        if snapshot is not None:
            raise ValueError(_("Independent backup inventory and freshness checks do not accept a snapshot ID."))
    elif not isinstance(snapshot, str) or not restic_backup.ID.fullmatch(snapshot):
        raise ValueError(_("Select a full Restic receipt ID for verification or source recovery."))
    if (action == 'recover-source') != (output is not None):
        raise ValueError(_("Source recovery requires --output NEW_DIRECTORY; other storage reads do not accept it."))
    value = load(path, target)
    destination = output_path(output) if output is not None else None
    if dry_run or action == 'recover-source' and not apply:
        result = {'target': target, 'sourceId': value['sourceId'], 'repository': value['repository'],
                  'action': action, 'snapshotId': snapshot, 'destination': str(destination) if destination else None,
                  'applied': False, 'readiness': 'not-checked'}
        if action == 'backup-check':
            result['maxAgeSeconds'] = max_age_seconds
        return result
    runtime(value)
    reader = restic_backup.Restic(value, owner_uid=os.geteuid(), repository_id=value['repositoryId'], read_only=True)
    if action == 'backups':
        return restic_backup.inventory_reader(reader)
    if action == 'backup-check':
        return restic_health.check(reader, max_age_seconds=max_age_seconds)
    if action == 'verify-backup':
        return restic_backup.verify_reader(reader, snapshot)
    reader, receipt, manifest = restic_backup.receipt_reader(reader, snapshot)
    archive, record = restic_backup.recipe_reader(reader, receipt, manifest)
    recovery_recipe.unpack(archive, destination)
    return {'target': target, 'sourceId': value['sourceId'], 'snapshotId': snapshot,
            'systemPath': record['systemPath'], 'destination': str(destination),
            'state': 'recorded-source-recovered', 'applied': True}
