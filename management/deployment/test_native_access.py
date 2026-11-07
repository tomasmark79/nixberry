"""Storage access survives source loss without importing a source service policy."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_access
import native_restic
import recovery_recipe


class AccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'access.json'
        self.value = {'version': 1, 'target': 'vm-demo-source', 'sourceId': 'libvirt:example',
            'repository': str(self.root / 'repository'), 'repositoryId': 'a' * 64,
            'passwordFile': str(self.root / 'password'), 'ssh': None,
            'runtimeDirectory': str(self.root / 'runtime'), 'timeoutSeconds': 120}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.value))
        self.path.chmod(0o600)

    def test_context_does_not_require_source_capture_catalogue_or_root_owner(self):
        self.assertEqual(native_access.load(self.path, 'vm-demo-source'), self.value)
        self.assertEqual(native_restic.snapshots.OWNER_UID, 0)
        if os.geteuid() != 0:
            with self.assertRaises(ValueError):
                native_restic.private_file(self.path)

    def test_wrong_identity_unknown_fields_store_paths_and_unsafe_context_permissions_fail(self):
        original = dict(self.value)
        for change in ({'target': 'foreign'}, {'repositoryId': 'not a full pin'}, {'applications': {}},
                       {'passwordFile': '/nix/store/secret'}, {'version': True}, {'ssh': {}}):
            with self.subTest(change=change):
                self.value.update(change)
                self.save()
                with self.assertRaises(ValueError):
                    native_access.load(self.path, 'vm-demo-source')
                self.value = dict(original)
                self.save()
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            native_access.load(self.path, 'vm-demo-source')

    def test_recovery_plan_neither_connects_nor_creates_output(self):
        destination = self.root / 'recovered'
        with patch('native_access.native_restic.Restic', side_effect=AssertionError('offline plan')):
            result = native_access.run(self.path, 'vm-demo-source', 'recover-source',
                                       snapshot='b' * 64, output=str(destination))
        self.assertFalse(result['applied'])
        self.assertEqual(result['readiness'], 'not-checked')
        self.assertFalse(destination.exists())

    def test_independent_health_reads_only_storage_with_an_explicit_threshold(self):
        password = Path(self.value['passwordFile'])
        password.write_text('test-only-password')
        password.chmod(0o600)
        calls = []
        def read(reader, arguments, **kwargs):
            self.assertTrue(reader.read_only)
            self.assertEqual(reader.repository_id, self.value['repositoryId'])
            self.assertEqual(reader.owner_uid, os.geteuid())
            calls.append(arguments)
            if arguments == ['cat', 'config']:
                return json.dumps({'id': self.value['repositoryId']})
            if arguments == ['snapshots', '--tag', native_restic.RECEIPT + ',source:' + self.value['sourceId']]:
                return '[]'
            self.fail('Unexpected repository operation: ' + repr(arguments))
        with patch('native_restic.Restic._execute', read), patch('native_restic.require_tmpfs'), \
                patch('snapshots.machine_id', side_effect=AssertionError('source must be independent')):
            result = native_access.run(self.path, 'vm-demo-source', 'backup-check', max_age_seconds=3600)
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['sourceId'], self.value['sourceId'])
        self.assertEqual(result['maxAgeSeconds'], 3600)
        self.assertIsNone(result['snapshotId'])
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.root / 'repository').exists())

    def test_health_dry_run_is_unverified_and_preserves_the_explicit_age_threshold(self):
        with patch('native_access.runtime', side_effect=AssertionError('dry-run created runtime')), \
                patch('native_restic.Restic', side_effect=AssertionError('dry-run opened storage')):
            result = native_access.run(self.path, 'vm-demo-source', 'backup-check',
                                       max_age_seconds=31536000, dry_run=True)
        self.assertEqual(result, {'target': 'vm-demo-source', 'sourceId': self.value['sourceId'],
            'repository': self.value['repository'], 'action': 'backup-check', 'snapshotId': None,
            'destination': None, 'applied': False, 'readiness': 'not-checked', 'maxAgeSeconds': 31536000})
        self.assertFalse((self.root / 'runtime').exists())

    def test_health_requires_a_positive_bounded_integer_age_before_opening_access(self):
        with patch('native_access.load', side_effect=AssertionError('invalid arguments opened access')):
            for age in (None, True, False, 0, -1, 31536001, 3600.0, '3600'):
                with self.subTest(age=age), self.assertRaises(ValueError):
                    native_access.run(self.path, 'vm-demo-source', 'backup-check', max_age_seconds=age)

    def test_age_threshold_is_rejected_for_other_independent_operations(self):
        with patch('native_access.load', side_effect=AssertionError('invalid arguments opened access')):
            for action in ('backups', 'verify-backup', 'recover-source'):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'age'):
                    native_access.run(self.path, 'vm-demo-source', action,
                        snapshot='b' * 64 if action != 'backups' else None,
                        output=str(self.root / 'recovered') if action == 'recover-source' else None,
                        max_age_seconds=3600)

    def test_health_is_read_only_and_does_not_accept_a_snapshot_or_output(self):
        with patch('native_access.load', side_effect=AssertionError('invalid arguments opened access')):
            for arguments in ({'apply': True}, {'snapshot': 'b' * 64}, {'output': str(self.root / 'recovered')}):
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    native_access.run(self.path, 'vm-demo-source', 'backup-check',
                                       max_age_seconds=3600, **arguments)

    def test_inventory_rejects_snapshot_and_existing_snapshot_actions_require_full_ids(self):
        with patch('native_access.load', side_effect=AssertionError('invalid arguments opened access')):
            for action, identifier in (('backups', 'b' * 64), ('verify-backup', None),
                                       ('verify-backup', 'b' * 8), ('recover-source', None),
                                       ('recover-source', 'b' * 8)):
                with self.subTest(action=action, identifier=identifier), self.assertRaises(ValueError):
                    native_access.run(self.path, 'vm-demo-source', action, snapshot=identifier)

    def test_readonly_reader_cannot_initialize_or_delete_repository_state(self):
        reader = native_restic.Restic(self.value, owner_uid=os.geteuid(), repository_id=self.value['repositoryId'], read_only=True)
        with patch('native_restic.subprocess.run', side_effect=AssertionError('must reject before execution')):
            for arguments in (['init'], ['backup', '/data'], ['forget', 'b'*64], ['unlock'], ['prune'],
                              ['stats'], ['stats', '--mode', 'raw-data', 'b'*64],
                              ['stats', '--mode', 'restore-size', 'latest'],
                              ['stats', '--mode', 'restore-size', 'b'*64, 'c'*64]):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, 'only'):
                    reader.run(arguments)
        self.assertIn('--no-lock', reader.command())

    def test_readonly_statistics_accept_one_explicit_payload(self):
        reader = native_restic.Restic(self.value, owner_uid=os.geteuid(), repository_id=self.value['repositoryId'], read_only=True)
        with patch.object(reader, '_execute', return_value='{"total_size":1024,"snapshots_count":1}') as execute:
            self.assertEqual(reader.run(['stats', '--mode', 'restore-size', 'b'*64]),
                             '{"total_size":1024,"snapshots_count":1}')
        self.assertEqual(execute.call_args.args[0], ['stats', '--mode', 'restore-size', 'b'*64])

    def test_metadata_reads_are_bounded_before_the_output_is_loaded_into_memory(self):
        import sys
        reader = native_restic.Restic(self.value, owner_uid=os.geteuid(), repository_id=self.value['repositoryId'], read_only=True)
        with patch.object(reader, 'command', return_value=[sys.executable, '-c', 'import sys; sys.stdout.write("x" * 100000)']), \
                patch('native_restic.require_tmpfs'), patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(RuntimeError):
                reader.run(['dump'], max_output=1024)
        self.assertEqual(list((self.root / 'runtime').iterdir()), [])

    def test_receipt_and_sealed_recipe_identity_are_checked_before_output_is_reserved(self):
        recipe = self.root / 'recipe'
        recipe.mkdir()
        for name in ('flake.nix', 'flake.lock', 'admin.pub'):
            (recipe / name).write_text('{}' if name == 'flake.lock' else 'fixture')
        metadata = {'format': recovery_recipe.FORMAT, 'sourceId': self.value['sourceId'],
                    'target': self.value['target'], 'systemPath': '/nix/store/'+'d'*32+'-nixos-system-test',
                    'policySha256': 'e' * 64}
        archive = self.root / 'recipe.tar'
        recovery_recipe.pack(recipe, metadata, archive)
        import hashlib
        manifest = {'recovery': {'path': '/old/recovery.tar', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                'systemPath': metadata['systemPath'], 'policySha256': metadata['policySha256']}}
        storage = type('Storage', (), {'run': lambda *args, **kwargs: archive.read_bytes(), 'settings': self.value})()
        original = dict(self.value)
        for change in ({'target': 'foreign'}, {'sourceId': 'foreign'}, {'policySha256': 'f'*64}, {}):
            destination = self.root / 'recovered'
            with self.subTest(change=change), patch('native_access.native_restic.receipt_reader', return_value=(storage, {'payloadId': 'c'*64}, manifest)), \
                    patch('native_access.native_restic.Restic'), patch('native_access.runtime'), \
                    patch('native_access.recovery_recipe.validate', return_value={**metadata, **change}):
                if change:
                    with self.assertRaises(ValueError):
                        native_access.run(self.path, 'vm-demo-source', 'recover-source', snapshot='b'*64, output=str(destination), apply=True)
                    self.assertFalse(destination.exists())
                else:
                    result = native_access.run(self.path, 'vm-demo-source', 'recover-source', snapshot='b'*64, output=str(destination), apply=True)
                    self.assertTrue(result['applied'])
                    self.assertEqual((destination / 'recipe/admin.pub').read_text(), 'fixture')
                    self.assertFalse((destination / 'INCOMPLETE').exists())
                    with self.assertRaises(FileExistsError):
                        native_access.run(self.path, 'vm-demo-source', 'recover-source', snapshot='b'*64, output=str(destination), apply=True)


class AccessCliTests(unittest.TestCase):
    def test_access_does_not_authorize_capture_update_or_other_writes(self):
        import cli
        for action in ('backup', 'backup-init', 'backup-stage', 'update', 'restore', 'reset'):
            with self.subTest(action=action), patch('cli.run_native_access') as run, \
                    patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as failure:
                cli.main([action, 'vm-demo-source', '--backup-access', '/private/access.json'])
            self.assertEqual(failure.exception.code, 2)
            run.assert_not_called()

    def test_missing_declaration_can_be_recovered_using_explicit_access_without_source_or_nix(self):
        import cli
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / 'registry.json'
            with patch.dict(os.environ, {'NIXBERRY_DEPLOYMENT': str(registry)}), \
                    patch('cli.run_native_access', return_value={'snapshots': []}) as run, \
                    patch('cli.run_native', side_effect=AssertionError('source must be offline')), \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(cli.main(['backups', 'vm-demo-lost', '--backup-access', '/private/access.json', '--json']), 0)
                self.assertEqual(json.loads(output.getvalue()), {'snapshots': []})
                self.assertEqual(run.call_args.args[:3], ('/private/access.json', 'vm-demo-lost', 'backups'))
