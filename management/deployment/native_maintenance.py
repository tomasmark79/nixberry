"""Check freshness and apply configuration-owned, machine-scoped native retention."""
import native_health
import native_restic
import native_retention
import snapshots
from messages import _


def policy(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'keepLast', 'maxAgeSeconds'}
            or type(value['version']) is not int or value['version'] != 1
            or value['keepLast'] is not None and (type(value['keepLast']) is not int or not 1 <= value['keepLast'] <= 100000)
            or value['maxAgeSeconds'] is not None and (type(value['maxAgeSeconds']) is not int
                                                     or not 1 <= value['maxAgeSeconds'] <= 31536000)):
        raise ValueError(_("Invalid native backup maintenance policy."))
    return value


def reader(settings):
    directory = snapshots.secure_directory(settings['runtimeDirectory'], create=True)
    native_restic.require_tmpfs(directory)
    return native_restic.Restic(settings, read_only=True)


def check(settings, maintenance):
    settings = native_restic.policy(settings)
    maintenance = policy(maintenance)
    if maintenance['maxAgeSeconds'] is None:
        raise ValueError(_("Configure nixberry.backup.restic.maxAgeSeconds before checking native backup freshness."))
    return native_health.check(reader(settings), max_age_seconds=maintenance['maxAgeSeconds'],
                               expected_machine_id=snapshots.machine_id())


def prune(settings, maintenance, expected_system, *, apply=False):
    settings = native_restic.policy(settings)
    maintenance = policy(maintenance)
    if maintenance['keepLast'] is None:
        raise ValueError(_("Configure nixberry.backup.restic.keepLast before pruning native backups."))
    machine = snapshots.machine_id()
    def current():
        if native_restic.running_system() != expected_system or snapshots.machine_id() != machine:
            raise RuntimeError(_("The source generation or machine identity changed; native retention was refused."))
    current()
    storage = reader(settings)
    def selection():
        return native_retention.plan(storage, keep_last=maintenance['keepLast'], machine_id=machine)
    if not apply:
        return {'applied': False, **selection()}
    with snapshots.lock(settings['capture']) as state:
        current()
        if snapshots.pending(settings['capture'], state):
            raise RuntimeError(_("Interrupted capture needs writer recovery before native retention."))
        report = selection()
        verified = None
        if report['remove']:
            # Metadata authenticates the deletion candidates. Fully read one
            # retained capture and its sealed recipe before deleting anything.
            verified = report['keep'][0]
            native_restic.verify_reader(storage, verified)
            if selection() != report:
                raise RuntimeError(_("Native backup inventory changed during retention; no snapshots were deleted."))
            current()
            writer = native_restic.Restic(settings)
            writer.deadline = storage.deadline
            writer.repository()
            writer.run(['forget', '--prune', '--', *report['remove'], *report['payloads']])
        return {'applied': True, 'verifiedSnapshotId': verified, **report}
