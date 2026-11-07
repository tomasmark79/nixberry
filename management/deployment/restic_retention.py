"""Plan explicit Restic retention by authenticated capture group and machine."""
import time

from messages import _
import restic_backup
import snapshots


def plan(reader, *, keep_last, machine_id, now=None):
    if type(keep_last) is not int or not 1 <= keep_last <= 100000:
        raise ValueError(_("Restic retention requires an explicit keepLast between 1 and 100000."))
    if not isinstance(machine_id, str) or not snapshots.ID.fullmatch(machine_id):
        raise ValueError(_("Restic retention requires the current source machine identity."))
    now = time.time() if now is None else now
    groups, other = {}, set()
    for item in restic_backup.inventory_reader(reader)['snapshots']:
        selected_reader, record, manifest = restic_backup.receipt_reader(reader, item['snapshotId'])
        if manifest['machineId'] != machine_id:
            other.add(manifest['machineId'])
            continue
        if record['createdAt'] > now + 300:
            raise ValueError(_("Future capture timestamps prevent Restic retention."))
        group = groups.setdefault(record['payloadId'], {'time': record['createdAt'], 'receipts': []})
        if group['time'] != record['createdAt']:
            raise ValueError(_("Completion receipts disagree about their shared Restic payload."))
        group['receipts'].append(item['snapshotId'])
    ordered = sorted(groups, key=lambda identifier: (groups[identifier]['time'], identifier), reverse=True)
    kept, removed = ordered[:keep_last], ordered[keep_last:]
    return {'sourceId': reader.settings['sourceId'], 'machineId': machine_id, 'keepLast': keep_last,
            'keep': [identifier for payload in kept for identifier in sorted(groups[payload]['receipts'])],
            'remove': [identifier for payload in removed for identifier in sorted(groups[payload]['receipts'])],
            'payloads': removed, 'untouchedMachines': sorted(other)}
