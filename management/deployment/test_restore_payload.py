"""Native restoration retains application metadata inside private staging."""
import os
from pathlib import Path
import struct
import tempfile
import unittest

import restore_payload


def evidence(users=(), groups=()):
    return {'architecture': 'x86_64-linux',
            'users': [{'name': name, 'uid': uid, 'gid': 0} for name, uid in users],
            'groups': [{'name': name, 'gid': gid} for name, gid in groups]}


class OwnershipTests(unittest.TestCase):
    def test_checks_only_used_ids_and_preserves_unknown_container_owners(self):
        source = evidence([('app', 200)], [('app', 300)])
        target = evidence([('app', 200), ('unrelated', 201)], [('app', 300)])
        check = restore_payload.ownership(source, target)
        check('uid', 200)
        check('gid', 300)
        check('uid', 123456)

    def test_refuses_numeric_account_reuse_missing_names_and_ambiguous_aliases(self):
        source = evidence([('app', 200)], [('app', 300)])
        targets = [evidence([('different', 200)], [('app', 300)]), evidence(),
                   evidence([('app', 200), ('app', 201)], [('app', 300)]),
                   evidence([('app', 200), ('alias', 200)], [('app', 300)])]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(ValueError):
                restore_payload.ownership(source, target)('uid', 200)
        with self.assertRaises(ValueError):
            restore_payload.ownership(evidence(), evidence([('new-user', 1000)]))('uid', 1000)

    def test_refuses_architecture_mismatch_and_legacy_missing_evidence(self):
        for target in ({**evidence(), 'architecture': 'aarch64-linux'}, None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                restore_payload.ownership(evidence(), target)

    def test_named_acl_ids_are_checked_in_addition_to_file_owners(self):
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, mode, owner) for tag, mode, owner in
                [(1, 7, 2**32-1), (2, 4, 200), (4, 5, 2**32-1),
                 (8, 4, 300), (16, 5, 2**32-1), (32, 0, 2**32-1)])
        check = restore_payload.ownership(evidence([('app', 200)], [('app', 300)]), evidence())
        with self.assertRaises(ValueError):
            restore_payload.acl_owners(acl, check)
        restore_payload.acl_owners(acl, restore_payload.ownership(evidence(), evidence()))
        for malformed in (b'', struct.pack('<I', 3), acl + b'\0',
                          struct.pack('<IHHI', 2, 2, 9, 200)):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                restore_payload.acl_owners(malformed, check)

    def test_staged_symlink_target_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'unchanged').write_text('keep')
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'link').symlink_to(outside)
            own = evidence([(str(os.getuid()), os.getuid())], [(str(os.getgid()), os.getgid())])
            restore_payload.inspect_tree(stage, restore_payload.ownership(own, own))
            self.assertEqual((outside / 'unchanged').read_text(), 'keep')


class StageTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import native_restic
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'stage'
        self.identity = evidence([('operator', os.getuid())], [('operator', os.getgid())])
        self.manifest = {'format': 'nixberry-restic-v2', 'identity': self.identity,
                         'machineId': 'a' * 32,
                         'paths': [{'source': '/var/lib/app', 'payload': '/payload/app'}]}
        self.receipt = {'sourceId': 'vm-demo-source', 'payloadId': 'b' * 64, 'manifestSha256': 'c' * 64}
        def restore(identifier, destination, paths):
            self.assertEqual(identifier, 'b' * 64)
            data = destination / 'payload/app'
            data.mkdir(parents=True)
            (data / 'example').write_text('application data')
        self.reader = SimpleNamespace(owner_uid=os.getuid(), restore=restore)
        root_user = patch('restore_payload.os.geteuid', return_value=0)
        root_user.start()
        self.addCleanup(root_user.stop)
        for item in (patch.object(native_restic, 'receipt_reader', return_value=(self.reader, self.receipt, self.manifest)),
                     patch.object(native_restic, 'recipe_reader', return_value=None)):
            item.start()
            self.addCleanup(item.stop)

    def test_nonroot_restore_is_rejected_before_any_storage_access(self):
        from unittest.mock import patch
        with patch('restore_payload.os.geteuid', return_value=1000), \
                patch('native_restic.receipt_reader', side_effect=AssertionError('storage access before root check')):
            with self.assertRaises(ValueError):
                restore_payload.stage(self.reader, 'd' * 64, self.destination, self.identity)
        self.assertFalse(self.destination.exists())

    def test_private_stage_contains_verified_data_and_never_a_live_completion(self):
        report = restore_payload.stage(self.reader, 'd' * 64, self.destination, self.identity)
        self.assertEqual(report['state'], 'staged-verified')
        self.assertEqual((Path(report['paths'][0]['staged']) / 'example').read_text(), 'application data')
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.destination / 'INCOMPLETE').exists())
        self.assertTrue((self.destination / 'stage.json').exists())
        self.assertEqual(sorted(entry.name for entry in self.destination.iterdir()), ['data', 'stage.json'])
        with self.assertRaises(FileExistsError):
            restore_payload.stage(self.reader, 'd' * 64, self.destination, self.identity)

    def test_old_manifest_and_wrong_architecture_do_not_create_staging(self):
        for version, architecture in (('nixberry-restic-v1', 'x86_64-linux'),
                                      ('nixberry-restic-v2', 'aarch64-linux')):
            self.manifest['format'] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                restore_payload.stage(self.reader, 'd' * 64, self.destination,
                                      {**self.identity, 'architecture': architecture})
            self.assertFalse(self.destination.exists())

    def test_owner_conflict_retains_only_an_incomplete_private_stage(self):
        target = evidence([('different', os.getuid())], [('operator', os.getgid())])
        with self.assertRaises(ValueError):
            restore_payload.stage(self.reader, 'd' * 64, self.destination, target)
        self.assertTrue((self.destination / 'INCOMPLETE').is_file())
        self.assertFalse((self.destination / 'stage.json').exists())

    def test_restore_error_retains_incomplete_marker_and_never_reuses_destination(self):
        def fail(*args):
            raise RuntimeError('storage unavailable')
        self.reader.restore = fail
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            restore_payload.stage(self.reader, 'd' * 64, self.destination, self.identity)
        self.assertTrue((self.destination / 'INCOMPLETE').is_file())
        with self.assertRaises(FileExistsError):
            restore_payload.stage(self.reader, 'd' * 64, self.destination, self.identity)


class RestoreCommandTests(unittest.TestCase):
    def test_empty_selection_cannot_restore_the_entire_payload(self):
        from unittest.mock import patch
        import native_restic
        reader = native_restic.Restic({'timeoutSeconds': 1}, read_only=True)
        with patch.object(reader, '_execute', side_effect=AssertionError('must fail before execution')):
            with self.assertRaises(ValueError):
                reader.restore('a' * 64, '/private/staging', [])

    def test_restore_uses_only_literal_selection_verification_and_readonly_storage(self):
        from unittest.mock import patch
        import subprocess
        import native_restic
        reader = native_restic.Restic({'timeoutSeconds': 1, 'repository': '/repo',
            'passwordFile': '/password', 'runtimeDirectory': '/run/private', 'ssh': None}, read_only=True)
        paths = [{'source': '/app', 'payload': '/snapshots/app[1]'},
                 {'source': '/app/database', 'payload': '/snapshots/db'}]
        with patch('native_restic.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as execute:
            reader.restore('a' * 64, '/private/staging', paths)
        command = execute.call_args.args[0]
        self.assertIn('--no-lock', command)
        self.assertIn('--verify', command)
        self.assertIn('--sparse', command)
        self.assertNotIn('--delete', command)
        self.assertEqual(command[command.index('restore'):], ['restore', 'a' * 64,
            '--target', '/private/staging', '--verify', '--sparse',
            '--include', '/snapshots/app\\[1\\]', '--include', '/snapshots/db',
            '--include', '!/snapshots/app\\[1\\]/database'])


class CapabilityTests(unittest.TestCase):
    def test_capability_namespace_owner_must_match_target_accounts(self):
        check = restore_payload.ownership(evidence([('namespace', 12345)]), evidence())
        namespace = struct.pack('<IIIIII', 0x03000001, 1, 0, 0, 0, 12345)
        with self.assertRaises(ValueError):
            restore_payload.capability_owners(namespace, check)
        restore_payload.capability_owners(namespace, restore_payload.ownership(evidence(), evidence()))
        root = struct.pack('<IIIII', 0x02000001, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            restore_payload.capability_owners(root, restore_payload.ownership(evidence([('root', 0)]), evidence()))
        for malformed in (b'', struct.pack('<I', 0x03000001), namespace + b'\0'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                restore_payload.capability_owners(malformed, check)
