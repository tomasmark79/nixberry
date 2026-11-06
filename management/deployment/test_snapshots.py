"""Snapshot capture failure paths keep writer recovery and publication separate."""
import copy
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import snapshots


def settings(directory):
    return {'version': 1, 'applications': {
        'web': {'paths': ['/var/lib/web'], 'stopUnits': ['web.service', 'worker.service']}},
        'roots': {'system': {'path': '/', 'snapshotDirectory': str(directory / 'snapshots')}},
        'stateDirectory': str(directory / 'state'), 'minFreeBytes': 1024, 'timeoutSeconds': 60}


class Host:
    def __init__(self):
        self.units = {'web.service': 'active', 'worker.service': 'inactive'}
        self.commands = []
        self.fail_snapshot = False
        self.fail_start = False
        self.fail_stop = False
        self.subvolumes = {}

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[:2] == ['systemctl', 'show']:
            return 'LoadState=loaded\nActiveState=' + self.units[command[-1]] + '\n'
        if command[:2] == ['systemctl', 'stop']:
            for unit in command[2:]:
                self.units[unit] = 'inactive'
            if self.fail_stop:
                raise RuntimeError('simulated stop failure after changing state')
        if command[:2] == ['systemctl', 'start']:
            if self.fail_start:
                raise RuntimeError('simulated restart failure')
            for unit in command[2:]:
                self.units[unit] = 'active'
        if command[:3] == ['btrfs', 'subvolume', 'snapshot']:
            if self.fail_snapshot:
                raise RuntimeError('simulated snapshot failure')
            Path(command[-1]).mkdir()
            self.subvolumes[command[-1]] = {'uuid': str(uuid.uuid4()),
                'parent': '11111111-1111-1111-1111-111111111111'}
        if command[:3] == ['btrfs', 'property', 'get']:
            return 'ro=true\n'
        if command[:3] == ['btrfs', 'subvolume', 'show']:
            value = self.subvolumes[command[-1]]
            return 'UUID: ' + value['uuid'] + '\nParent UUID: ' + value['parent'] + '\n'
        if command[:3] == ['btrfs', 'subvolume', 'delete']:
            Path(command[-1]).rmdir()
            del self.subvolumes[command[-1]]
        return ''


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = settings(self.root)
        self.host = Host()
        self.identity = patch('snapshots.machine_id', return_value='a' * 32)
        self.identity.start()
        self.addCleanup(self.identity.stop)
        self.owner = patch('snapshots.OWNER_UID', os.getuid())
        self.owner.start()
        self.addCleanup(self.owner.stop)
        self.executor = patch('snapshots.run', self.host)
        self.executor.start()
        self.addCleanup(self.executor.stop)
        self.topology = {'roots': {'system': {
            **self.policy['roots']['system'], 'filesystemUuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            'subvolumeUuid': '11111111-1111-1111-1111-111111111111'}},
            'paths': [{'source': '/var/lib/web', 'root': 'system', 'relative': 'var/lib/web'}]}
        self.inspector = patch('snapshots.inspect', side_effect=lambda *args, **kwargs: copy.deepcopy(self.topology))
        self.inspect = self.inspector.start()
        self.addCleanup(self.inspector.stop)
        fs = patch('snapshots.filesystem', return_value={'uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'filesystemPath': '/snapshot'})
        fs.start()
        self.addCleanup(fs.stop)
        mounted = patch('snapshots.mounted_snapshot', return_value=False)
        mounted.start()
        self.addCleanup(mounted.stop)

    def test_plan_does_not_create_files_or_stop_services(self):
        report = snapshots.capture(self.policy)
        self.assertFalse(report['applied'])
        self.assertEqual(report['paths'], self.topology['paths'])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(any(command[:2] in (['systemctl', 'stop'], ['systemctl', 'start'])
                             for command in self.host.commands))

    def test_capture_resumes_only_previously_active_writers_and_publishes_readonly(self):
        report = snapshots.capture(self.policy, apply=True)
        self.assertEqual(report['state'], 'ready')
        self.assertRegex(report['snapshotId'], '^[0-9a-f]{32}$')
        self.assertEqual(self.host.units, {'web.service': 'active', 'worker.service': 'inactive'})
        self.assertIn(['systemctl', 'stop', 'web.service', 'worker.service'], self.host.commands)
        self.assertIn(['systemctl', 'start', 'web.service'], self.host.commands)
        capture = next(command for command in self.host.commands if command[:3] == ['btrfs', 'subvolume', 'snapshot'])
        self.assertEqual(capture[3], '-r')
        self.assertFalse((self.root / 'state/pending.json').exists())
        listed = snapshots.inventory(self.policy)
        self.assertEqual([row['snapshotId'] for row in listed['snapshots']], [report['snapshotId']])
        self.assertEqual((self.root / 'state').stat().st_mode & 0o777, 0o700)
        record = self.root / 'state/records' / (report['snapshotId'] + '.json')
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)

    def test_snapshot_failure_resumes_writers_and_never_publishes_ready(self):
        self.host.fail_snapshot = True
        with self.assertRaises(RuntimeError):
            snapshots.capture(self.policy, apply=True)
        self.assertEqual(self.host.units['web.service'], 'active')
        self.assertFalse((self.root / 'state/pending.json').exists())
        self.assertEqual(snapshots.inventory(self.policy)['snapshots'], [])

    def test_partial_stop_failure_still_resumes_original_writers(self):
        self.host.fail_stop = True
        with self.assertRaises(RuntimeError):
            snapshots.capture(self.policy, apply=True)
        self.assertEqual(self.host.units, {'web.service': 'active', 'worker.service': 'inactive'})
        self.assertFalse((self.root / 'state/pending.json').exists())
        self.assertFalse(any(c[:3] == ['btrfs', 'subvolume', 'snapshot'] for c in self.host.commands))

    def test_activation_units_stop_and_resume_with_the_original_writer_state(self):
        self.policy['applications']['web']['stopUnits'] += ['web.socket', 'web.timer']
        self.host.units.update({'web.socket': 'active', 'web.timer': 'inactive'})
        record = snapshots.capture(self.policy, apply=True)
        self.assertEqual(record['state'], 'ready')
        self.assertIn(['systemctl', 'start', 'web.service', 'web.socket'], self.host.commands)
        self.assertEqual(self.host.units['web.timer'], 'inactive')
        for unit in ('sshd.socket', 'NetworkManager.timer', 'network.target'):
            with self.assertRaises(ValueError):
                snapshots.unit_name(unit)

    def test_writer_reactivation_prevents_ready_publication(self):
        original = self.host.__call__
        def run(command, **kwargs):
            output = original(command, **kwargs)
            if command[:3] == ['btrfs', 'subvolume', 'snapshot']:
                self.host.units['worker.service'] = 'active'
            return output
        with patch('snapshots.run', run), self.assertRaisesRegex(RuntimeError, 'writers'):
            snapshots.capture(self.policy, apply=True)
        self.assertEqual(snapshots.inventory(self.policy)['snapshots'], [])

    def test_preflight_failure_never_stops_writers_or_creates_state(self):
        self.inspect.side_effect = ValueError('simulated missing filesystem')
        with self.assertRaises(ValueError):
            snapshots.capture(self.policy, apply=True)
        self.assertEqual(self.host.commands, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_restart_failure_retains_pending_record_and_blocks_new_capture(self):
        self.host.fail_start = True
        with self.assertRaises(RuntimeError):
            snapshots.capture(self.policy, apply=True)
        self.assertTrue((self.root / 'state/pending.json').exists())
        self.assertEqual(snapshots.inventory(self.policy)['snapshots'], [])
        with self.assertRaisesRegex(RuntimeError, 'recovery'):
            snapshots.capture(self.policy, apply=True)
        self.host.fail_start = False
        report = snapshots.resume(self.policy, apply=True)
        self.assertEqual(report['resumedUnits'], ['web.service'])
        self.assertFalse((self.root / 'state/pending.json').exists())

    def test_source_identity_change_after_stop_resumes_without_snapshot(self):
        different = copy.deepcopy(self.topology)
        different['roots']['system']['subvolumeUuid'] = 'different'
        self.inspect.side_effect = [copy.deepcopy(self.topology), different]
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            snapshots.capture(self.policy, apply=True)
        self.assertEqual(self.host.units['web.service'], 'active')
        self.assertFalse(any(command[:3] == ['btrfs', 'subvolume', 'snapshot'] for command in self.host.commands))

    def test_policy_rejects_unsafe_paths_units_and_unknown_fields(self):
        for change in ({'stateDirectory': '/var/../tmp/state'}, {'extra': True}, {'timeoutSeconds': True},
                       {'roots': {}}, {'applications': {'bad': {'paths': ['/'], 'stopUnits': []}}},
                       {'applications': {'bad': {'paths': ['/var/lib/web'], 'stopUnits': ['sshd.service']}}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                snapshots.policy({**self.policy, **change})

    def test_state_symlinks_and_foreign_recovery_identity_fail_closed(self):
        (self.root / 'state').symlink_to(self.root / 'elsewhere')
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            snapshots.capture(self.policy, apply=True)
        (self.root / 'state').unlink()
        self.host.fail_start = True
        with self.assertRaises(RuntimeError):
            snapshots.capture(self.policy, apply=True)
        marker = self.root / 'state/pending.json'
        data = json.loads(marker.read_text())
        data['machineId'] = 'b' * 32
        marker.write_text(json.dumps(data))
        self.host.fail_start = False
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            snapshots.resume(self.policy, apply=True)

    def test_nested_subvolumes_must_be_declared_when_application_includes_them(self):
        with self.assertRaisesRegex(ValueError, 'subvolume'):
            snapshots.coverage(['/var/lib/web'], {'system': '/'}, ['/var/lib/web/database'])
        self.assertEqual(snapshots.coverage(['/var/lib/web'],
                         {'system': '/', 'database': '/var/lib/web/database'},
                         ['/var/lib/web/database']), ['database', 'system'])

    def test_snapshot_removal_plans_then_deletes_only_verified_recorded_subvolumes(self):
        with patch('snapshots.mountpoints', return_value=[]):
            record = snapshots.capture(self.policy, apply=True)
            path = Path(record['roots']['system']['snapshot'])
            plan = snapshots.remove(self.policy, record['snapshotId'])
            self.assertFalse(plan['applied'])
            self.assertTrue(path.exists())
            result = snapshots.remove(self.policy, record['snapshotId'], apply=True)
            self.assertEqual(result['state'], 'deleted')
            self.assertFalse(path.exists())
            self.assertEqual(snapshots.inventory(self.policy)['snapshots'], [])

    def test_removal_refuses_replaced_snapshot_and_unknown_identifiers(self):
        with patch('snapshots.mountpoints', return_value=[]):
            record = snapshots.capture(self.policy, apply=True)
            path = record['roots']['system']['snapshot']
            self.host.subvolumes[path]['uuid'] = str(uuid.uuid4())
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                snapshots.remove(self.policy, record['snapshotId'], apply=True)
            with self.assertRaises(ValueError):
                snapshots.remove(self.policy, '../elsewhere', apply=True)
            self.assertFalse(any(command[:3] == ['btrfs', 'subvolume', 'delete'] for command in self.host.commands))

    def test_timeout_recovers_writers_before_returning_failure(self):
        clock = unittest.mock.Mock(return_value=10)
        def inspect(*args, **kwargs):
            if kwargs:
                clock.return_value = 1000
            return copy.deepcopy(self.topology)
        self.inspect.side_effect = inspect
        with patch('snapshots.time.monotonic', clock), self.assertRaises(TimeoutError):
            snapshots.capture(self.policy, apply=True)
        self.assertEqual(self.host.units['web.service'], 'active')
        self.assertFalse((self.root / 'state/pending.json').exists())

    def test_multiple_roots_share_a_capture_id_and_one_writer_outage(self):
        self.policy['roots']['database'] = {'path': '/var/lib/web/database',
                                          'snapshotDirectory': self.policy['roots']['system']['snapshotDirectory']}
        self.topology['roots']['database'] = {**self.policy['roots']['database'], 'filesystemUuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            'subvolumeUuid': '11111111-1111-1111-1111-111111111111'}
        record = snapshots.capture(self.policy, apply=True)
        self.assertEqual(len(record['roots']), 2)
        self.assertEqual(sum(c[:2] == ['systemctl', 'stop'] for c in self.host.commands), 1)
        commands = self.host.commands
        resume_index = next(i for i, c in enumerate(commands) if c[:2] == ['systemctl', 'start'])
        self.assertEqual(sum(c[:3] == ['btrfs', 'subvolume', 'snapshot'] for c in commands[:resume_index]), 2)

    def test_corrupted_ready_record_is_not_reported_as_a_valid_capture(self):
        record = snapshots.capture(self.policy, apply=True)
        path = self.root / 'state/records' / (record['snapshotId'] + '.json')
        for change in ({'extra': True}, {'createdAt': True}, {'roots': []}, {'paths': [{}]}):
            original = json.loads(path.read_text())
            path.write_text(json.dumps({**original, **change}))
            with self.subTest(change=change), self.assertRaises(ValueError):
                snapshots.inventory(self.policy)
            path.write_text(json.dumps(original))

    def test_help_translates_explicit_czech_but_does_not_follow_lc_all(self):
        from test_messages import czech_catalogue
        for language, expected in [('en', 'Capture configured application data'),
                                   ('cs', 'Zachycení nakonfigurovaných dat aplikací')]:
            output = io.StringIO()
            with patch.dict(os.environ, {'NIXBERRY_LANG': language, 'LC_ALL': 'cs_CZ.UTF-8'}), \
                    patch('messages._catalog', return_value=czech_catalogue() if language == 'cs' else __import__('gettext').NullTranslations()), \
                    contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                snapshots.main(['--help'])
            self.assertEqual(exit.exception.code, 0)
            self.assertIn(expected, output.getvalue())


class SnapshotCommandTests(unittest.TestCase):
    def test_external_stderr_is_preserved_on_success_failure_and_timeout(self):
        for outcome in (subprocess.CompletedProcess(['test-only'], 0, 'technical-data', 'external warning\n'),
                        subprocess.CompletedProcess(['test-only'], 1, '', 'external failure\n'),
                        subprocess.TimeoutExpired(['test-only'], 2, stderr=b'external timeout\n')):
            stderr = io.StringIO()
            with patch('snapshots.subprocess.run', side_effect=outcome if isinstance(outcome, Exception) else None,
                       return_value=outcome), contextlib.redirect_stderr(stderr):
                if isinstance(outcome, Exception) or outcome.returncode:
                    with self.assertRaises((RuntimeError, TimeoutError)):
                        snapshots.run(['test-only'])
                else:
                    self.assertEqual(snapshots.run(['test-only']), 'technical-data')
            self.assertIn('external ', stderr.getvalue())
            self.assertNotIn('[nixberry]', stderr.getvalue())
