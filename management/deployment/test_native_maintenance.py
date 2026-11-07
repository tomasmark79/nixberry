"""Source maintenance preserves a verified capture and excludes concurrent writers."""
import contextlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import native_maintenance


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.system = '/nix/store/' + 'a' * 32 + '-nixos-system-vm-demo-source'
        self.settings = {'capture': {'stateDirectory': '/state'}, 'sourceId': 'vm-demo-source'}
        self.policy = {'version': 1, 'keepLast': 2, 'maxAgeSeconds': 3600}
        self.selection = {'sourceId': 'vm-demo-source', 'machineId': 'a' * 32, 'keepLast': 2,
                          'keep': ['b' * 64], 'remove': ['c' * 64], 'payloads': ['d' * 64],
                          'untouchedMachines': []}
        self.reader = SimpleNamespace(deadline=123, read_only=True)
        self.writer = Mock()
        self.events = []
        self.writer.run.side_effect = lambda args, **kwargs: self.events.append(('write', args))
        patches = [patch('native_maintenance.native_restic.policy', side_effect=lambda value: value),
                   patch('native_maintenance.native_restic.running_system', return_value=self.system),
                   patch('native_maintenance.snapshots.machine_id', return_value='a' * 32),
                   patch('native_maintenance.snapshots.pending', return_value=False),
                   patch('native_maintenance.snapshots.lock', side_effect=self.lock),
                   patch('native_maintenance.reader', return_value=self.reader),
                   patch('native_maintenance.native_retention.plan', side_effect=lambda *a, **k: dict(self.selection)),
                   patch('native_maintenance.native_restic.verify_reader', side_effect=lambda *a: self.events.append(('verify', a[1]))),
                   patch('native_maintenance.native_restic.Restic', return_value=self.writer)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    @contextlib.contextmanager
    def lock(self, settings):
        self.events.append(('lock', None))
        yield Path('/state')
        self.events.append(('unlock', None))

    def test_preview_is_read_only_without_a_capture_lease_or_full_payload_scan(self):
        result = native_maintenance.prune(self.settings, self.policy, self.system)
        self.assertFalse(result['applied'])
        self.assertEqual(result['remove'], ['c' * 64])
        self.assertEqual(self.events, [])
        self.writer.run.assert_not_called()

    def test_apply_verifies_the_retained_capture_before_explicit_paired_deletion(self):
        result = native_maintenance.prune(self.settings, self.policy, self.system, apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(result['verifiedSnapshotId'], 'b' * 64)
        self.assertEqual(self.events, [('lock', None), ('verify', 'b' * 64),
            ('write', ['forget', '--prune', '--', 'c' * 64, 'd' * 64]), ('unlock', None)])
        self.assertEqual(self.writer.deadline, self.reader.deadline)
        self.writer.repository.assert_called_once_with()

    def test_failed_verification_pending_capture_or_changed_selection_never_delete(self):
        with patch('native_maintenance.native_restic.verify_reader', side_effect=ValueError('corrupt')):
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                native_maintenance.prune(self.settings, self.policy, self.system, apply=True)
        with patch('native_maintenance.snapshots.pending', return_value=True):
            with self.assertRaises(RuntimeError):
                native_maintenance.prune(self.settings, self.policy, self.system, apply=True)
        changed = {**self.selection, 'remove': ['e' * 64]}
        with patch('native_maintenance.native_retention.plan', side_effect=[self.selection, changed]):
            with self.assertRaises(RuntimeError):
                native_maintenance.prune(self.settings, self.policy, self.system, apply=True)
        self.writer.run.assert_not_called()

    def test_generation_and_machine_are_rechecked_within_the_lease(self):
        for probe in ('native_maintenance.native_restic.running_system', 'native_maintenance.snapshots.machine_id'):
            initial = self.system if 'running_system' in probe else 'a' * 32
            with self.subTest(probe=probe), patch(probe, side_effect=[initial, 'changed']):
                with self.assertRaises(RuntimeError):
                    native_maintenance.prune(self.settings, self.policy, self.system, apply=True)
        self.writer.run.assert_not_called()

    def test_disabled_or_malformed_maintenance_has_no_implicit_deletion_default(self):
        for value in ({'version': 1, 'keepLast': None, 'maxAgeSeconds': None},
                      {**self.policy, 'keepLast': 0}, {**self.policy, 'keepLast': True},
                      {**self.policy, 'maxAgeSeconds': 31536001}, {**self.policy, 'unknown': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                native_maintenance.prune(self.settings, value, self.system, apply=True)
        self.writer.run.assert_not_called()

    def test_health_uses_read_only_metadata_and_current_machine_without_capture_lock(self):
        with patch('native_maintenance.native_health.check', return_value={'status': 'missing'}) as check:
            result = native_maintenance.check(self.settings, self.policy)
            check.assert_called_once_with(self.reader, max_age_seconds=3600,
                                          expected_machine_id='a' * 32, include_statistics=False)
        self.assertEqual(result, {'status': 'missing'})
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
