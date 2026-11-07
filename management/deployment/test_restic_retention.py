"""Restic retention owns complete capture groups for exactly one source machine."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import restic_retention


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.machine = 'a' * 32
        self.reader = SimpleNamespace(settings={'sourceId': 'vm-demo-source'})
        self.entries = {}
        for n in range(1, 5):
            identifier = format(n, '064x')
            self.entries[identifier] = ({'payloadId': format(n + 100, '064x'), 'createdAt': n * 10},
                                       {'machineId': self.machine})
        foreign = 'f' * 64
        self.entries[foreign] = ({'payloadId': 'e' * 64, 'createdAt': 1000}, {'machineId': 'b' * 32})
        self.history = list(self.entries)
        def receipt(reader, identifier):
            record, manifest = self.entries[identifier]
            return reader, copy.deepcopy(record), copy.deepcopy(manifest)
        for item in (patch('restic_retention.restic_backup.inventory_reader', side_effect=lambda reader:
                          {'snapshots': [{'snapshotId': identifier} for identifier in self.history]}),
                     patch('restic_retention.restic_backup.receipt_reader', side_effect=receipt)):
            item.start()
            self.addCleanup(item.stop)

    def test_preview_keeps_distinct_newest_payloads_and_leaves_other_machines_untouched(self):
        report = restic_retention.plan(self.reader, keep_last=2, machine_id=self.machine, now=1000)
        self.assertEqual(report['keep'], [format(4, '064x'), format(3, '064x')])
        self.assertEqual(report['remove'], [format(2, '064x'), format(1, '064x')])
        self.assertEqual(report['payloads'], [format(102, '064x'), format(101, '064x')])
        self.assertEqual(report['untouchedMachines'], ['b' * 32])

    def test_duplicate_completion_receipts_cannot_delete_a_retained_payload(self):
        duplicate = 'd' * 64
        self.entries[duplicate] = copy.deepcopy(self.entries[format(4, '064x')])
        self.history.append(duplicate)
        report = restic_retention.plan(self.reader, keep_last=1, machine_id=self.machine, now=1000)
        self.assertEqual(set(report['keep']), {format(4, '064x'), duplicate})
        self.assertNotIn(format(104, '064x'), report['payloads'])
        self.assertEqual(len(report['payloads']), 3)

    def test_bad_limits_future_metadata_and_corrupt_receipts_refuse_a_prune_plan(self):
        for keep in (None, False, 0, -1, 100001):
            with self.subTest(keep=keep), self.assertRaises(ValueError):
                restic_retention.plan(self.reader, keep_last=keep, machine_id=self.machine, now=1000)
        self.entries[format(1, '064x')][0]['createdAt'] = 99999
        with self.assertRaises(ValueError):
            restic_retention.plan(self.reader, keep_last=1, machine_id=self.machine, now=1000)
        with patch('restic_retention.restic_backup.receipt_reader', side_effect=ValueError('corrupt')):
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                restic_retention.plan(self.reader, keep_last=1, machine_id=self.machine, now=1000)
