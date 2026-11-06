"""Read authenticated backup freshness without verifying application payload files."""
import math
import time

from messages import _
import native_restic
import snapshots

MAX_AGE_SECONDS = 31536000
CLOCK_SKEW_SECONDS = 300


def check(reader, *, max_age_seconds, now=None, expected_machine_id=None):
    """Check metadata and the selected sealed recipe using read-only storage access.

    Source-local callers supply their current machine ID. Independent callers
    omit it to inspect the newest capture across source machine replacements.
    Invalid candidate receipts or manifests raise instead of masking damaged
    evidence with an older healthy capture. Application payload verification
    remains a separate operation.
    """
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= MAX_AGE_SECONDS:
        raise ValueError(_("Backup freshness requires a positive age limit of at most one year in seconds."))
    if (expected_machine_id is not None
            and (not isinstance(expected_machine_id, str) or not snapshots.ID.fullmatch(expected_machine_id))):
        raise ValueError(_("Backup freshness requires a valid expected source machine identity."))
    if getattr(reader, 'read_only', False) is not True:
        raise ValueError(_("Backup freshness requires a read-only repository reader."))
    now = time.time() if now is None else now
    if (type(now) not in (int, float) or now < 0
            or type(now) is float and not math.isfinite(now)):
        raise ValueError(_("Backup freshness requires a finite nonnegative current timestamp."))

    result = {'status': 'missing', 'sourceId': reader.settings['sourceId'],
              'snapshotId': None, 'createdAt': None, 'ageSeconds': None,
              'maxAgeSeconds': max_age_seconds, 'machineId': None}
    selected = None
    for item in native_restic.inventory_reader(reader)['snapshots']:
        identifier = item['snapshotId']
        authenticated_reader, receipt, manifest = native_restic.receipt_reader(reader, identifier)
        if expected_machine_id is not None and manifest['machineId'] != expected_machine_id:
            continue
        # Inventory timestamps are headers, not the authenticated capture time.
        key = (receipt['createdAt'], identifier)
        if selected is None or key > selected[0]:
            selected = key, authenticated_reader, receipt, manifest
    if selected is None:
        return result

    key, authenticated_reader, receipt, manifest = selected
    native_restic.recipe_reader(authenticated_reader, receipt, manifest)
    created = receipt['createdAt']
    age = int(max(0, now - created))
    status = ('future' if created > now + CLOCK_SKEW_SECONDS
              else 'stale' if now - created > max_age_seconds else 'ok')
    result.update(status=status, snapshotId=key[1], createdAt=created,
                  ageSeconds=age, machineId=manifest['machineId'])
    return result
