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

    def test_readonly_reader_cannot_initialize_or_delete_repository_state(self):
        reader = native_restic.Restic(self.value, owner_uid=os.geteuid(), repository_id=self.value['repositoryId'], read_only=True)
        with patch('native_restic.subprocess.run', side_effect=AssertionError('must reject before execution')):
            for arguments in (['init'], ['backup', '/data'], ['forget', 'b'*64], ['unlock'], ['prune']):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, 'only'):
                    reader.run(arguments)
        self.assertIn('--no-lock', reader.command())

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
