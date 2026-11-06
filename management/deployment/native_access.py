"""Read native encrypted backups using independent, private storage access."""
import json
import os
from pathlib import Path
import sys

from messages import _
import native_restic
import recovery_recipe
import snapshots

ACTIONS = ('backups', 'verify-backup', 'recover-source')


def load(path, target):
    path = Path(path).expanduser().absolute()
    native_restic.runtime_path(str(path))
    value = json.loads(native_restic.private_file(path, owner_uid=os.geteuid(), limit=1024 * 1024))
    required = {'version', 'target', 'sourceId', 'repository', 'repositoryId', 'passwordFile',
                'ssh', 'runtimeDirectory', 'timeoutSeconds'}
    if (not isinstance(value, dict) or set(value) != required or type(value['version']) is not int
            or value['version'] != 1 or value['target'] != target
            or not isinstance(target, str) or not recovery_recipe.NAME.fullmatch(target)
            or not isinstance(value['repositoryId'], str) or not native_restic.ID.fullmatch(value['repositoryId'])):
        raise ValueError(_("Invalid native backup access context or source identity."))
    native_restic.storage_policy(value)
    paths = [str(path), value['passwordFile'], value['runtimeDirectory']]
    if value['ssh'] is not None:
        paths += [value['ssh']['identityFile'], value['ssh']['knownHostsFile']]
    recipe = os.environ.get('NIXBERRY_RECIPE')
    if recipe and any(Path(item).resolve().is_relative_to(Path(recipe).resolve()) for item in paths):
        raise ValueError(_("Keep backup access contexts and runtime credentials outside the recipe."))
    return value


def runtime(value):
    directory = snapshots.secure_directory(value['runtimeDirectory'], create=True, owner_uid=os.geteuid())
    native_restic.require_tmpfs(directory)


def output_path(value):
    destination = Path(os.path.abspath(Path(value).expanduser()))
    native_restic.runtime_path(str(destination))
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(_("Recovery requires a new output directory; existing data is never overwritten."))
    snapshots.secure_directory(destination.parent, private=False, owner_uid=os.geteuid())
    recipe = os.environ.get('NIXBERRY_RECIPE')
    if recipe and destination.is_relative_to(Path(recipe).resolve()):
        raise ValueError(_("Choose a recovery output directory outside the recipe."))
    return destination


def run(path, target, action, *, snapshot=None, output=None, apply=False, dry_run=False):
    if sys.platform != 'linux':
        raise RuntimeError(_("Native backup access currently requires Linux."))
    if action not in ACTIONS:
        raise ValueError(_("This operation does not support independent native backup access."))
    if apply and action != 'recover-source':
        raise ValueError(_("Independent inventory and verification are read-only and do not accept --apply."))
    if (action == 'backups' and snapshot is not None
            or action != 'backups' and (not isinstance(snapshot, str) or not native_restic.ID.fullmatch(snapshot))):
        raise ValueError(_("Select a full Restic receipt ID for verification or source recovery."))
    if (action == 'recover-source') != (output is not None):
        raise ValueError(_("Source recovery requires --output NEW_DIRECTORY; other storage reads do not accept it."))
    value = load(path, target)
    destination = output_path(output) if output is not None else None
    if dry_run or action == 'recover-source' and not apply:
        return {'target': target, 'sourceId': value['sourceId'], 'repository': value['repository'],
                'action': action, 'snapshotId': snapshot, 'destination': str(destination) if destination else None,
                'applied': False, 'readiness': 'not-checked'}
    runtime(value)
    reader = native_restic.Restic(value, owner_uid=os.geteuid(), repository_id=value['repositoryId'], read_only=True)
    if action == 'backups':
        return native_restic.inventory_reader(reader)
    if action == 'verify-backup':
        return native_restic.verify_reader(reader, snapshot)
    reader, receipt, manifest = native_restic.receipt_reader(reader, snapshot)
    archive, record = native_restic.recipe_reader(reader, receipt, manifest)
    recovery_recipe.unpack(archive, destination)
    return {'target': target, 'sourceId': value['sourceId'], 'snapshotId': snapshot,
            'systemPath': record['systemPath'], 'destination': str(destination),
            'state': 'recorded-source-recovered', 'applied': True}
