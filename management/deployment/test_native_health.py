"""Freshness uses authenticated capture metadata without changing backup state."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import native_restic
import recovery_recipe
import native_health


class NativeHealthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        password = self.root / 'password'
        password.write_text('test-only-password')
        password.chmod(0o600)
        self.settings = {'sourceId': 'libvirt:vm-demo-source', 'target': 'vm-demo-source',
                         'repository': str(self.root / 'repository'), 'passwordFile': str(password),
                         'runtimeDirectory': str(self.root / 'runtime'), 'timeoutSeconds': 120, 'ssh': None}
        self.reader = native_restic.Restic(self.settings, owner_uid=os.geteuid(),
                                          repository_id='a' * 64, read_only=True)
        self.calls = []
        self.entries = {}
        self.headers = []
        self.repository_id = 'a' * 64
        self.statistics = {'total_size': 4096, 'total_file_count': 3, 'snapshots_count': 1}
        self.system = '/nix/store/' + 'd' * 32 + '-nixos-system-vm-demo-source'
        recipe = self.root / 'recipe'
        recipe.mkdir()
        for name in ('flake.nix', 'flake.lock', 'admin.pub'):
            (recipe / name).write_text('{}' if name == 'flake.lock' else 'fixture')
        self.recipe = recipe
        runner = patch.object(self.reader, '_execute', side_effect=self.execute)
        runner.start()
        self.addCleanup(runner.stop)
        self.runner = runner

    def check(self, **kwargs):
        return native_health.check(self.reader, max_age_seconds=kwargs.pop('max_age_seconds', 3600),
                                   now=kwargs.pop('now', 10000), **kwargs)

    def add_capture(self, created=9900, *, machine='1' * 32, header_time=None, version=None):
        number = len(self.entries) + 1
        identifier, payload = format(number, '064x'), format(number + 1000, '064x')
        archive = self.root / ('recipe-' + str(number) + '.tar')
        recovery_recipe.pack(self.recipe, {'format': recovery_recipe.FORMAT,
            'sourceId': self.settings['sourceId'], 'target': self.settings['target'],
            'systemPath': self.system, 'policySha256': 'e' * 64}, archive)
        archive_bytes = archive.read_bytes()
        manifest = {'format': version or native_restic.FORMAT, 'sourceId': self.settings['sourceId'],
                    'machineId': machine, 'attemptId': format(number, '032x'), 'createdAt': created,
                    'captureMethod': 'cold', 'paths': [{'source': '/srv/app', 'payload': '/srv/app'}],
                    'capture': None, 'recovery': {'path': '/run/nixberry/recovery.tar',
                        'sha256': hashlib.sha256(archive_bytes).hexdigest(),
                        'systemPath': self.system, 'policySha256': 'e' * 64}}
        if manifest['format'] == native_restic.FORMAT:
            manifest['identity'] = {'architecture': 'x86_64-linux', 'users': [], 'groups': []}
        receipt = {'format': manifest['format'], 'sourceId': self.settings['sourceId'],
                   'attemptId': manifest['attemptId'], 'createdAt': created, 'payloadId': payload,
                   'manifestPath': '/run/nixberry/manifest.json',
                   'manifestSha256': hashlib.sha256(native_restic.encode(manifest).encode()).hexdigest()}
        header = {'id': identifier, 'time': datetime.fromtimestamp(
            created if header_time is None else header_time, timezone.utc).isoformat(),
            'tags': [native_restic.RECEIPT, 'source:' + self.settings['sourceId']]}
        self.entries[identifier] = {'receipt': receipt, 'manifest': manifest,
                                    'archive': archive_bytes, 'header': header}
        self.headers.append(header)
        return identifier

    def execute(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if arguments[:3] == ['stats', '--mode', 'restore-size']:
            self.assertIn(arguments[3], [e['receipt']['payloadId'] for e in self.entries.values()])
            return json.dumps(self.statistics)
        if arguments == ['cat', 'config']:
            return json.dumps({'id': self.repository_id})
        if arguments == ['snapshots', '--tag', native_restic.RECEIPT + ',source:' + self.settings['sourceId']]:
            return json.dumps(self.headers)
        if arguments[0] == 'snapshots':
            return json.dumps([self.entries[arguments[1]]['header']])
        if arguments[0] == 'dump':
            for identifier, entry in self.entries.items():
                receipt = entry['receipt']
                if arguments == ['dump', identifier, '/nixberry-receipt.json']:
                    return native_restic.encode(receipt)
                if arguments == ['dump', receipt['payloadId'], receipt['manifestPath']]:
                    return native_restic.encode(entry['manifest'])
                if arguments == ['dump', receipt['payloadId'], entry['manifest']['recovery']['path']]:
                    self.assertTrue(kwargs['binary'])
                    self.assertEqual(kwargs['max_output'], recovery_recipe.MAX_ARCHIVE)
                    return entry['archive']
        self.fail('Unexpected external operation: ' + repr(arguments))

    def resign(self, identifier):
        entry = self.entries[identifier]
        entry['receipt']['manifestSha256'] = hashlib.sha256(
            native_restic.encode(entry['manifest']).encode()).hexdigest()

    def test_missing_has_no_invented_capture_or_age_and_checks_repository_pin(self):
        self.assertEqual(self.check(), {'status': 'missing', 'sourceId': self.settings['sourceId'],
            'snapshotId': None, 'createdAt': None, 'ageSeconds': None, 'maxAgeSeconds': 3600,
            'machineId': None})
        self.assertEqual(self.calls[0][0], ['cat', 'config'])

    def test_latest_authenticated_capture_wins_over_inventory_time_and_order(self):
        newest = self.add_capture(9900, header_time=1)
        self.add_capture(1000, header_time=20000)
        result = self.check()
        self.assertEqual(result, {'status': 'ok', 'sourceId': self.settings['sourceId'],
            'snapshotId': newest, 'createdAt': 9900, 'ageSeconds': 100, 'maxAgeSeconds': 3600,
            'machineId': '1' * 32})
        archives = [call for call, _ in self.calls if call[-1] == '/run/nixberry/recovery.tar']
        self.assertEqual(archives, [['dump', self.entries[newest]['receipt']['payloadId'], '/run/nixberry/recovery.tar']])

    def test_age_at_limit_is_healthy_and_above_limit_is_stale(self):
        self.add_capture(6400)
        self.assertEqual(self.check()['status'], 'ok')
        self.assertEqual(self.check(now=10001)['status'], 'stale')

    def test_optional_statistics_count_distinct_completed_payloads_for_current_machine(self):
        latest = self.add_capture(9900, header_time=1)
        self.add_capture(9800, header_time=20000)
        self.add_capture(9950, machine='2' * 32)
        self.headers.append(self.headers[0])
        result = self.check(expected_machine_id='1' * 32, include_statistics=True)
        self.assertEqual(result['snapshotId'], latest)
        self.assertEqual(result['statistics'], {'restorePoints': 2, 'latestLogicalBytes': 4096})
        self.assertEqual([a for a, _ in self.calls if a[0] == 'stats'], [
            ['stats', '--mode', 'restore-size', self.entries[latest]['receipt']['payloadId']]])

    def test_optional_statistics_report_zero_points_without_inventing_a_size_when_missing(self):
        result = self.check(include_statistics=True)
        self.assertEqual(result['statistics'], {'restorePoints': 0, 'latestLogicalBytes': None})
        self.assertFalse(any(a[0] == 'stats' for a, _ in self.calls))

    def test_optional_statistics_reject_invalid_sizes_and_aggregated_results(self):
        self.add_capture()
        for value in ({}, {'total_size': True, 'snapshots_count': 1},
                      {'total_size': -1, 'snapshots_count': 1},
                      {'total_size': '4096', 'snapshots_count': 1},
                      {'total_size': 4096, 'snapshots_count': 2},
                      {'total_size': 4096, 'snapshots_count': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.statistics = value
                self.check(include_statistics=True)

    def test_statistics_preserve_stale_status_and_accept_measured_zero_bytes(self):
        self.add_capture(100)
        self.statistics['total_size'] = 0
        result = self.check(include_statistics=True)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['statistics'], {'restorePoints': 1, 'latestLogicalBytes': 0})

    def test_source_local_health_uses_only_the_current_authenticated_machine(self):
        current = self.add_capture(6400)
        self.add_capture(9900, machine='2' * 32)
        result = self.check(now=10001, expected_machine_id='1' * 32)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['snapshotId'], current)
        self.assertEqual(result['machineId'], '1' * 32)

    def test_absent_current_machine_is_missing_and_does_not_validate_unselected_recipe(self):
        old = self.add_capture(machine='2' * 32)
        self.entries[old]['archive'] = b'not the selected machine'
        result = self.check(expected_machine_id='1' * 32)
        self.assertEqual(result['status'], 'missing')
        self.assertIsNone(result['snapshotId'])
        self.assertIsNone(result['machineId'])
        self.assertFalse(any(call[-1] == '/run/nixberry/recovery.tar' for call, _ in self.calls))

    def test_independent_health_accepts_latest_authenticated_machine_without_local_identity(self):
        self.add_capture(1000)
        latest = self.add_capture(9900, machine='2' * 32)
        with patch('snapshots.machine_id', side_effect=AssertionError('independent check contacted source')):
            result = self.check()
        self.assertEqual(result['snapshotId'], latest)
        self.assertEqual(result['machineId'], '2' * 32)

    def test_allowed_clock_skew_clamps_age_and_excessive_future_time_is_unhealthy(self):
        identifier = self.add_capture(10300)
        self.assertEqual(self.check()['status'], 'ok')
        self.assertEqual(self.check()['ageSeconds'], 0)
        entry = self.entries[identifier]
        entry['receipt']['createdAt'] = entry['manifest']['createdAt'] = 10301
        self.resign(identifier)
        result = self.check()
        self.assertEqual(result['status'], 'future')
        self.assertEqual(result['ageSeconds'], 0)

    def test_a_future_capture_is_not_hidden_by_an_older_healthy_capture(self):
        self.add_capture(9900)
        future = self.add_capture(20000)
        self.assertEqual(self.check()['snapshotId'], future)
        self.assertEqual(self.check()['status'], 'future')

    def test_both_manifest_versions_remain_readable(self):
        for version in native_restic.FORMATS:
            with self.subTest(version=version):
                self.entries.clear()
                self.headers.clear()
                for archive in self.root.glob('recipe-*.tar'):
                    archive.unlink()
                self.add_capture(version=version)
                self.assertEqual(self.check()['status'], 'ok')

    def test_invalid_age_limits_fail_before_repository_reads(self):
        for value in (True, False, 0, -1, 31536001, 3600.0, '3600', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(max_age_seconds=value)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.check(max_age_seconds=31536000)['maxAgeSeconds'], 31536000)

    def test_invalid_expected_machine_ids_fail_before_repository_reads(self):
        for value in ('', 'invalid', 'A' * 32, '1' * 64, True, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(expected_machine_id=value)
        self.assertEqual(self.calls, [])

    def test_writable_reader_is_rejected_before_any_external_operation(self):
        self.reader.read_only = False
        with self.assertRaisesRegex(ValueError, 'read-only'):
            self.check()
        self.assertEqual(self.calls, [])

    def test_wrong_repository_identity_fails_even_when_inventory_is_empty(self):
        self.repository_id = 'f' * 64
        with self.assertRaisesRegex(RuntimeError, 'repository identity'):
            self.check()
        self.assertEqual(len(self.calls), 1)

    def test_sftp_identity_and_pinned_host_file_are_required_before_inventory(self):
        identity, known_hosts = self.root / 'identity', self.root / 'known-hosts'
        for path in (identity, known_hosts):
            path.write_text('test-only')
            path.chmod(0o600)
        self.settings.update(repository='sftp:reader@backup.invalid:/repository',
            ssh={'port': 2222, 'identityFile': str(identity), 'knownHostsFile': str(known_hosts)})
        with patch('native_restic.subprocess.run', return_value=subprocess.CompletedProcess([], 255, '', '')), \
                self.assertRaisesRegex(RuntimeError, 'SFTP trust or authentication'):
            self.check()
        self.assertEqual(self.calls, [])
        with patch('native_restic.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '/', '')) as execute:
            self.assertEqual(self.check()['status'], 'missing')
        command = execute.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertIn('UserKnownHostsFile=' + str(known_hosts), command)
        self.assertIn('IdentitiesOnly=yes', command)
        self.assertIn(str(identity), command)
        self.assertIn('2222', command)

    def test_read_only_metadata_size_limit_applies_to_health_inventory(self):
        self.runner.stop()
        script = ('import sys; sys.stdout.write(\'{"id":"' + self.repository_id
                  + '"}\' if sys.argv[1] == "cat" else "x" * (16 * 1024 * 1024 + 1))')
        with patch.object(self.reader, 'command', return_value=[sys.executable, '-c', script]), \
                patch('native_restic.require_tmpfs'), patch('sys.stderr', new_callable=io.StringIO), \
                self.assertRaises(RuntimeError):
            self.check()
        self.assertEqual(list((self.root / 'runtime').iterdir()), [])

    def test_default_clock_uses_current_time_and_invalid_overrides_fail_before_reads(self):
        self.add_capture(9900)
        with patch('native_health.time.time', return_value=10000.25):
            self.assertEqual(native_health.check(self.reader, max_age_seconds=3600)['ageSeconds'], 100)
        self.calls.clear()
        for value in (True, '10000', float('nan'), float('inf'), float('-inf'), -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(now=value)
        self.assertEqual(self.calls, [])

    def test_malformed_inventory_and_foreign_source_receipts_fail_closed(self):
        identifier = self.add_capture()
        original = copy.deepcopy(self.headers)
        self.headers[0]['time'] = 'not a timestamp'
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.check()
        self.headers[:] = original
        self.entries[identifier]['receipt']['sourceId'] = 'foreign-source'
        with self.assertRaisesRegex(ValueError, 'completion receipt'):
            self.check()

    def test_corrupt_source_receipt_cannot_fall_back_to_a_healthy_capture(self):
        self.add_capture(9900)
        corrupt = self.add_capture(1000)
        self.entries[corrupt]['receipt']['createdAt'] = True
        with self.assertRaisesRegex(ValueError, 'completion receipt'):
            self.check()

    def test_unauthenticated_machine_metadata_cannot_be_skipped(self):
        self.add_capture(9900)
        corrupt = self.add_capture(1000, machine='2' * 32)
        self.entries[corrupt]['manifest']['machineId'] = '3' * 32
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.check(expected_machine_id='1' * 32)

    def test_invalid_authenticated_manifest_fields_fail_closed(self):
        identifier = self.add_capture()
        for change in ({'machineId': 'invalid'}, {'createdAt': 1}, {'identity': {}}, {'paths': []}):
            original = copy.deepcopy(self.entries[identifier]['manifest'])
            with self.subTest(change=change):
                self.entries[identifier]['manifest'].update(change)
                self.resign(identifier)
                with self.assertRaises(ValueError):
                    self.check()
            self.entries[identifier]['manifest'] = original
            self.resign(identifier)

    def test_selected_recovery_archive_digest_and_sealed_contents_are_both_authenticated(self):
        identifier = self.add_capture()
        entry = self.entries[identifier]
        entry['archive'] = b'corrupt archive'
        with self.assertRaisesRegex(ValueError, 'recovery configuration'):
            self.check()
        entry['manifest']['recovery']['sha256'] = hashlib.sha256(entry['archive']).hexdigest()
        self.resign(identifier)
        with self.assertRaisesRegex(ValueError, 'recovery recipe archive'):
            self.check()

    def test_sealed_recovery_recipe_must_match_the_selected_target_and_policy(self):
        identifier = self.add_capture()
        entry = self.entries[identifier]
        entry['manifest']['recovery']['policySha256'] = 'f' * 64
        self.resign(identifier)
        with self.assertRaisesRegex(ValueError, 'sealed recovery recipe'):
            self.check()

    def test_only_read_operations_are_used_and_application_payload_is_not_streamed(self):
        self.add_capture()
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        self.check()
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        self.assertEqual(before, after)
        self.assertTrue(all(call[0] in ('cat', 'snapshots', 'dump') for call, _ in self.calls))
        self.assertFalse(any(call[0] == 'dump' and call[-1] == '/' for call, _ in self.calls))
        self.assertIn('--no-lock', self.reader.command())


if __name__ == '__main__':
    unittest.main()
