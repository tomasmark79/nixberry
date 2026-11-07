"""Check freshness and apply configuration-owned, machine-scoped Restic retention."""
import restic_health
import restic_backup
import restic_retention
import snapshots
from messages import _


def policy(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'keepLast', 'maxAgeSeconds'}
            or type(value['version']) is not int or value['version'] != 1
            or value['keepLast'] is not None and (type(value['keepLast']) is not int or not 1 <= value['keepLast'] <= 100000)
            or value['maxAgeSeconds'] is not None and (type(value['maxAgeSeconds']) is not int
                                                     or not 1 <= value['maxAgeSeconds'] <= 31536000)):
        raise ValueError(_("Invalid Restic backup maintenance policy."))
    return value


def reader(settings):
    directory = snapshots.secure_directory(settings['runtimeDirectory'], create=True)
    restic_backup.require_tmpfs(directory)
    return restic_backup.Restic(settings, read_only=True)


def check(settings, maintenance, *, include_statistics=False):
    settings = restic_backup.policy(settings)
    maintenance = policy(maintenance)
    if maintenance['maxAgeSeconds'] is None:
        raise ValueError(_("Configure nixberry.backup.restic.maxAgeSeconds before checking Restic backup freshness."))
    return restic_health.check(reader(settings), max_age_seconds=maintenance['maxAgeSeconds'],
                               expected_machine_id=snapshots.machine_id(), include_statistics=include_statistics)


def prune(settings, maintenance, expected_system, *, apply=False):
    settings = restic_backup.policy(settings)
    maintenance = policy(maintenance)
    if maintenance['keepLast'] is None:
        raise ValueError(_("Configure nixberry.backup.restic.keepLast before pruning Restic backups."))
    machine = snapshots.machine_id()
    def current():
        if restic_backup.running_system() != expected_system or snapshots.machine_id() != machine:
            raise RuntimeError(_("The source generation or machine identity changed; Restic retention was refused."))
    current()
    storage = reader(settings)
    def selection():
        return restic_retention.plan(storage, keep_last=maintenance['keepLast'], machine_id=machine)
    if not apply:
        return {'applied': False, **selection()}
    with snapshots.lock(settings['capture']) as state:
        current()
        if snapshots.pending(settings['capture'], state):
            raise RuntimeError(_("Interrupted capture needs writer recovery before Restic retention."))
        report = selection()
        verified = None
        if report['remove']:
            # Metadata authenticates the deletion candidates. Fully read one
            # retained capture and its sealed recipe before deleting anything.
            verified = report['keep'][0]
            restic_backup.verify_reader(storage, verified)
            if selection() != report:
                raise RuntimeError(_("Restic backup inventory changed during retention; no snapshots were deleted."))
            current()
            writer = restic_backup.Restic(settings)
            writer.deadline = storage.deadline
            writer.repository()
            writer.run(['forget', '--prune', '--', *report['remove'], *report['payloads']])
        return {'applied': True, 'verifiedSnapshotId': verified, **report}
