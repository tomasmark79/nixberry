"""Native Restic backup keeps encrypted data and successful publication distinct."""
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_restic
import snapshots
from test_snapshots import Host, settings
from check_inventory import nix
from scaffold import new_vm
from test_support import copy_test_recipe


class PayloadMappingTests(unittest.TestCase):
    def test_separately_captured_children_hide_only_the_parent_underlay(self):
        paths = [{'source': '/live/app', 'payload': '/snapshots/parent/app'},
                 {'source': '/live/app/database', 'payload': '/snapshots/database'},
                 {'source': '/live/app/database/log', 'payload': '/snapshots/log'},
                 {'source': '/live/app/cache', 'payload': '/snapshots/parent/app/cache'},
                 {'source': '/live/application', 'payload': '/snapshots/parent/application'}]
        self.assertEqual(native_restic.hidden_payload_paths(paths),
                         ['/snapshots/database/log', '/snapshots/parent/app/database',
                          '/snapshots/parent/app/database/log'])

    def test_cold_and_same_root_child_paths_do_not_exclude_live_data(self):
        cold = [{'source': source, 'payload': source} for source in ('/app', '/app/data', '/app/data/nested')]
        same_root = [{'source': source, 'payload': '/snapshot' + source} for source in ('/app', '/app/data')]
        self.assertEqual(native_restic.hidden_payload_paths(cold), [])
        self.assertEqual(native_restic.hidden_payload_paths(same_root), [])


class NativeResticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.capture = settings(self.root)
        self.data = self.root / 'data'
        self.data.mkdir()
        (self.data / 'example').write_text('application data')
        self.capture['applications']['web']['paths'] = [str(self.data)]
        self.password = self.root / 'password'
        self.password.write_text('test-only')
        self.password.chmod(0o600)
        self.repository_id = self.root / 'repository-id'
        self.repository_id.write_text('a' * 64)
        self.repository_id.chmod(0o600)
        self.recovery = self.root / 'recovery.tar'
        self.recovery.write_bytes(b'test-only recipe')
        self.recovery.chmod(0o600)
        self.policy = {'version': 1, 'sourceId': 'vm-demo-server', 'captureMethod': 'cold',
            'capture': self.capture, 'repository': str(self.root / 'repository'),
            'passwordFile': str(self.password), 'repositoryIdFile': str(self.repository_id),
            'recoveryFile': str(self.recovery), 'runtimeDirectory': str(self.root / 'runtime'),
            'timeoutSeconds': 600, 'ssh': None}
        self.system = '/nix/store/' + 'd' * 32 + '-nixos-system-vm-demo-server'
        self.recovery.write_bytes(self.staged_archive())
        Path(str(self.recovery) + '.json').write_text(json.dumps({
            'version': 1, 'sourceId': self.policy['sourceId'], 'systemPath': self.system,
            'policySha256': hashlib.sha256(native_restic.encode(self.policy).encode()).hexdigest(),
            'archiveSha256': hashlib.sha256(self.recovery.read_bytes()).hexdigest()}))
        Path(str(self.recovery) + '.json').chmod(0o600)
        self.host = Host()
        patches = [patch('snapshots.OWNER_UID', os.getuid()),
                   patch('native_restic.running_system', return_value=self.system),
                   patch('snapshots.machine_id', return_value='1' * 32),
                   patch('snapshots.run', self.host),
                   patch('native_restic.require_tmpfs'),
                   patch('snapshots.cold_identity', return_value={'source': 'test-only'})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.calls = []
        self.data_id = 'b' * 64
        self.receipt_id = 'c' * 64
        self.saved = {}
        def restic(instance, arguments, **kwargs):
            self.calls.append(arguments)
            if arguments == ['cat', 'config']:
                return json.dumps({'id': 'a' * 64})
            if arguments[0] == 'backup' and '--stdin' not in arguments:
                self.assertEqual(self.host.units['web.service'],
                                 'active' if self.policy['captureMethod'] == 'snapshot' else 'inactive')
                manifest = next(Path(path) for path in arguments if path.endswith('/manifest.json'))
                self.saved['manifest'] = manifest.read_text()
                return json.dumps({'message_type': 'summary', 'snapshot_id': self.data_id})
            if arguments[0] == 'backup' and '--stdin' in arguments:
                self.assertEqual(self.host.units['web.service'], 'active')
                self.saved['receipt'] = kwargs['input']
                return json.dumps({'message_type': 'summary', 'snapshot_id': self.receipt_id})
            if arguments[:2] == ['dump', self.data_id]:
                if arguments[-1].endswith('/manifest.json'):
                    return self.saved['manifest']
                if arguments[-1] == str(self.recovery):
                    return self.recovery.read_bytes()
            raise AssertionError(arguments)
        self.runner = patch.object(native_restic.Restic, 'run', restic)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def test_restic_metadata_commands_do_not_inherit_the_callers_terminal_input(self):
        self.runner.stop()
        with patch('native_restic.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as execute:
            native_restic.Restic(self.policy).run(['cat', 'config'])
            self.assertEqual(execute.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(execute.call_args.kwargs['start_new_session'])

    def test_plan_does_not_create_state_or_stop_writers(self):
        result = native_restic.backup(self.policy)
        self.assertFalse(result['applied'])
        self.assertFalse((self.root / 'state').exists())
        self.assertFalse((self.root / 'runtime').exists())
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_cold_backup_stops_writers_until_payload_and_publishes_after_recovery(self):
        result = native_restic.backup(self.policy, apply=True)
        self.assertEqual(result['snapshotId'], self.receipt_id)
        receipt = json.loads(self.saved['receipt'])
        self.assertEqual(receipt['payloadId'], self.data_id)
        self.assertEqual(receipt['sourceId'], 'vm-demo-server')
        self.assertEqual(self.host.units['worker.service'], 'inactive')
        self.assertEqual(result['state'], 'recorded-captured')
        self.assertFalse((self.root / 'state/pending.json').exists())
        self.assertEqual(list((self.root / 'runtime').iterdir()), [])

    def test_wrong_repository_identity_never_stops_writers(self):
        self.repository_id.write_text('f' * 64)
        with self.assertRaisesRegex(RuntimeError, 'repository'):
            native_restic.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_writer_recovery_failure_never_publishes_a_completion_receipt(self):
        self.host.fail_start = True
        with self.assertRaises(RuntimeError):
            native_restic.backup(self.policy, apply=True)
        self.assertFalse(any('--stdin' in call for call in self.calls))
        self.assertTrue((self.root / 'state/pending.json').exists())

    def test_payload_failure_recovers_writers_without_publishing_completion(self):
        with patch('native_restic.payload', side_effect=RuntimeError('simulated transport failure')):
            with self.assertRaises(RuntimeError):
                native_restic.backup(self.policy, apply=True)
        self.assertEqual(self.host.units['web.service'], 'active')
        self.assertFalse((self.root / 'state/pending.json').exists())
        self.assertFalse(any('--stdin' in call for call in self.calls))

    def test_snapshot_lease_blocks_removal_until_encrypted_upload_finishes(self):
        self.policy['captureMethod'] = 'snapshot'
        contents = self.staged_archive()
        native_restic.stage(self.policy, self.system, archive=contents,
                            expected_sha256=hashlib.sha256(contents).hexdigest(), apply=True)
        topology = {'roots': {'system': {**self.capture['roots']['system'],
            'filesystemUuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            'subvolumeUuid': '11111111-1111-1111-1111-111111111111'}},
            'paths': [{'source': str(self.data), 'root': 'system', 'relative': str(self.data.relative_to('/'))}]}
        original_payload = native_restic.payload
        def payload(*args):
            capture = args[-1]
            with self.assertRaisesRegex(RuntimeError, 'lock'):
                snapshots.remove(self.capture, capture['snapshotId'], apply=True)
            return original_payload(*args)
        with patch('snapshots.inspect', side_effect=lambda *args, **kwargs: copy.deepcopy(topology)), \
                patch('snapshots.filesystem', return_value={'uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                                                           'filesystemPath': '/snapshot'}), \
                patch('snapshots.mounted_snapshot', return_value=False), \
                patch('snapshots.mountpoints', return_value=[]), \
                patch('native_restic.payload', payload):
            result = native_restic.backup(self.policy, apply=True)
        self.assertEqual(result['state'], 'recorded-captured')
        self.assertEqual(snapshots.inventory(self.capture)['snapshots'], [])
        with patch('snapshots.machine_id', return_value='2' * 32):
            native_restic.validate_manifest(json.loads(self.saved['receipt']), json.loads(self.saved['manifest']))

    def test_authenticated_manifest_rejects_changed_logical_paths_and_recovery_binding(self):
        native_restic.backup(self.policy, apply=True)
        receipt = json.loads(self.saved['receipt'])
        manifest = json.loads(self.saved['manifest'])
        native_restic.validate_manifest(receipt, manifest)
        for change in ({'format': 'unknown'}, {'paths': [{'source': '/etc/passwd', 'payload': '/unrelated'}]},
                       {'recovery': {'path': '/../escape', 'sha256': 'f' * 64}}, {'captureMethod': 'snapshot'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                native_restic.validate_manifest(receipt, {**manifest, **change})

    def test_sftp_probe_fails_promptly_before_any_writer_stop(self):
        self.policy.update(repository='sftp:backup@nas.example:/backup', ssh={
            'port': 7922, 'identityFile': str(self.password), 'knownHostsFile': str(self.password)})
        with patch('native_restic.subprocess.run', return_value=subprocess.CompletedProcess([], 255, '', '')) as execute:
            with self.assertRaisesRegex(RuntimeError, 'SFTP'):
                native_restic.backup(self.policy, apply=True)
            self.assertEqual(execute.call_args.args[0][0], 'sftp')
            self.assertLessEqual(execute.call_args.kwargs['timeout'], 30)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_stale_recovery_archive_binding_refuses_capture_before_shutdown(self):
        with patch('native_restic.running_system', return_value='/nix/store/' + 'e' * 32 + '-nixos-system-new'):
            with self.assertRaisesRegex(RuntimeError, 'recovery'):
                native_restic.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_generation_change_before_capture_lock_is_rechecked_before_writer_shutdown(self):
        new = '/nix/store/' + 'e' * 32 + '-nixos-system-new'
        with patch('native_restic.running_system', side_effect=[self.system, new]):
            with self.assertRaisesRegex(RuntimeError, 'recovery'):
                native_restic.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_recovery_staging_plans_and_refuses_wrong_running_generation(self):
        metadata = Path(str(self.recovery) + '.json')
        metadata.unlink()
        plan = native_restic.stage(self.policy, self.system)
        self.assertFalse(plan['applied'])
        self.assertFalse(metadata.exists())
        with self.assertRaises(RuntimeError):
            native_restic.stage(self.policy, '/nix/store/' + 'e' * 32 + '-nixos-system-wrong', apply=True)
        self.assertFalse(metadata.exists())
        native_restic.stage(self.policy, self.system, apply=True)
        self.assertEqual(json.loads(metadata.read_text())['systemPath'], self.system)

    def staged_archive(self):
        import recovery_recipe
        tree = self.root / 'recipe'
        tree.mkdir(exist_ok=True)
        for name in ('flake.nix', 'flake.lock', 'admin.pub'):
            (tree / name).write_text('{}' if name == 'flake.lock' else 'test-only')
        filename = self.root / 'received.tar'
        recovery_recipe.pack(tree, {'format': recovery_recipe.FORMAT, 'sourceId': self.policy['sourceId'],
            'target': 'vm-demo-server', 'systemPath': self.system,
            'policySha256': hashlib.sha256(native_restic.encode(self.policy).encode()).hexdigest()}, filename)
        contents = filename.read_bytes()
        filename.unlink()
        return contents

    def test_existing_unstructured_archive_cannot_be_rebound_as_recovery_code(self):
        self.recovery.write_bytes(b'not a sealed recipe archive')
        with self.assertRaises(ValueError):
            native_restic.stage(self.policy, self.system, apply=True)

    def test_received_archive_is_bound_atomically_only_after_hash_and_generation_checks(self):
        contents = self.staged_archive()
        checksum = hashlib.sha256(contents).hexdigest()
        before = self.recovery.read_bytes()
        with self.assertRaises(ValueError):
            native_restic.stage(self.policy, self.system, archive=contents, expected_sha256='0' * 64, apply=True)
        self.assertEqual(self.recovery.read_bytes(), before)
        with snapshots.lock(self.capture), self.assertRaisesRegex(RuntimeError, 'lock'):
            native_restic.stage(self.policy, self.system, archive=contents, expected_sha256=checksum, apply=True)
        self.assertEqual(self.recovery.read_bytes(), before)
        report = native_restic.stage(self.policy, self.system, archive=contents, expected_sha256=checksum, apply=True)
        self.assertTrue(report['applied'])
        self.assertEqual(self.recovery.read_bytes(), contents)
        self.assertEqual(native_restic.recovery(self.policy)['archiveSha256'], checksum)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_received_archive_with_a_valid_hash_but_foreign_policy_cannot_replace_configuration(self):
        contents = self.staged_archive()
        checksum = hashlib.sha256(contents).hexdigest()
        before = self.recovery.read_bytes()
        different = copy.deepcopy(self.policy)
        different['sourceId'] = 'vm-demo-other'
        with self.assertRaises(ValueError):
            native_restic.stage(different, self.system, archive=contents, expected_sha256=checksum, apply=True)
        self.assertEqual(self.recovery.read_bytes(), before)

    def test_unsafe_password_permissions_and_storage_paths_fail_closed(self):
        self.password.chmod(0o644)
        with self.assertRaises(ValueError):
            native_restic.backup(self.policy, apply=True)
        self.password.chmod(0o600)
        for field, value in [('passwordFile', '/nix/store/plaintext-password'),
                             ('repository', str(self.data / 'recursive-backup')),
                             ('sourceId', 'invalid,tag'), ('timeoutSeconds', True)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                native_restic.policy({**self.policy, field: value})

    def test_sftp_requires_explicit_private_identity_and_pinned_host_file(self):
        with self.assertRaises(ValueError):
            native_restic.policy({**self.policy, 'repository': 'sftp:backup@nas.example:/backup'})
        value = copy.deepcopy(self.policy)
        value.update(repository='sftp:backup@nas.example:/backup', ssh={
            'port': 7922, 'identityFile': '/var/lib/backup/ssh-key',
            'knownHostsFile': '/var/lib/backup/known_hosts'})
        value = native_restic.policy(value)
        with self.assertRaises(ValueError):
            native_restic.policy({**value, 'repository': 'sftp:backup@nas.example:/backup/../other'})
        command = native_restic.Restic(value).command()
        option = next(item for item in command if item.startswith('sftp.command='))
        for expected in ('StrictHostKeyChecking=yes', 'BatchMode=yes', 'IdentitiesOnly=yes',
                         'ForwardAgent=no', 'IdentityAgent=none', '/var/lib/backup/known_hosts'):
            self.assertIn(expected, option)


class NativePolicySystemTests(unittest.TestCase):
    def test_configuration_owns_policy_for_all_platforms_and_both_filesystems(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'recipe'
            copy_test_recipe(Path(__file__).resolve().parents[2], root)
            (root / 'configurations/server.nix').write_text('''{ ... }: {
              imports = [ ../modules/common.nix ];
              nixberry.backup = { enable=true; applications.web.paths=[ "/var/lib/web" ];
                restic = { enable=true; repository="sftp:backup@storage.example:/backup";
                  ssh={ port=7922; identityFile="/var/lib/backup/key"; knownHostsFile="/var/lib/backup/known_hosts"; }; };
              };
            }''')
            expected = {}
            for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
                for filesystem in ('ext4', 'btrfs'):
                    name = 'vm-demo-' + backend + '-' + filesystem
                    path, _ = new_vm(root, {}, {}, name, backend, offline=True,
                                     configuration='server', filesystem=filesystem)
                    declaration = json.loads(subprocess.check_output(
                        ['nix', 'eval', '--json', '--impure', '--expr', 'import ' + str(path)], text=True))
                    expected[name] = (backend + ':' + declaration['spec']['uuid'], filesystem)
            original = (root / 'examples/rpi.nix').read_text()
            serial = json.loads(subprocess.check_output(
                ['nix', 'eval', '--json', '--impure', '--expr', '(import ' + str(root / 'examples/rpi.nix') + ').hardwareSerial'], text=True))
            for filesystem in ('ext4', 'btrfs'):
                name = 'rpi-demo-' + filesystem
                text = original.rsplit('}', 1)[0] + 'configuration="server"; rootFilesystem="' + filesystem + '"; }'
                (root / 'machines/rpi' / (name + '.nix')).write_text(text)
                expected[name] = ('rpi:' + serial, filesystem)
            path, _ = new_vm(root, {}, {}, 'vm-demo-hyperv-legacy', 'hyperv', offline=True, configuration='server')
            declaration = json.loads(subprocess.check_output(
                ['nix', 'eval', '--json', '--impure', '--expr', 'import ' + str(path)], text=True))
            declaration['spec'].pop('uuid')
            path.write_text(nix(declaration))
            expected['vm-demo-hyperv-legacy'] = ('hyperv:' + declaration['spec']['ownerId'], 'ext4')
            expression = 'let f=builtins.getFlake ' + nix('path:' + str(root)) + '; in builtins.mapAttrs (_: s: s.config.system.build.nixberryBackupPolicy) f.nixberrySystems'
            result = subprocess.run(['nix', 'eval', '--json', '--impure', '--no-write-lock-file', '--expr', expression],
                                    capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])
            values = json.loads(result.stdout)
            self.assertEqual(set(values), set(expected))
            for name, value in values.items():
                self.assertEqual(value['sourceId'], expected[name][0], name)
                self.assertEqual(value['captureMethod'], 'snapshot' if expected[name][1] == 'btrfs' else 'cold', name)
                self.assertEqual(value['capture']['applications']['web'], {'paths': ['/var/lib/web'], 'stopUnits': []}, name)
                self.assertEqual(value['repository'], 'sftp:backup@storage.example:/backup', name)
                self.assertEqual(value['ssh']['port'], 7922, name)
            from native_controller import projection
            for name in ('vm-demo-libvirt-btrfs', 'rpi-demo-ext4'):
                selected = projection(str(root), name)
                self.assertTrue(selected['enabled'])
                self.assertEqual(selected['restic'], values[name])
                self.assertEqual(selected['capture'], values[name]['capture'])
                self.assertRegex(selected['systemPath'], r'^/nix/store/[a-z0-9]{32}-nixos-system-')


class RecoveryInputTests(unittest.TestCase):
    def test_an_incomplete_transfer_times_out_without_waiting_for_eof(self):
        read_descriptor, write_descriptor = os.pipe()
        self.addCleanup(os.close, write_descriptor)
        with os.fdopen(read_descriptor, 'rb') as stream, self.assertRaises(TimeoutError):
            native_restic.read_archive(stream, timeout=0.02)

    def test_transfer_size_is_bounded_before_archive_parsing(self):
        read_descriptor, write_descriptor = os.pipe()
        os.write(write_descriptor, b'x' * 33)
        os.close(write_descriptor)
        with os.fdopen(read_descriptor, 'rb') as stream, patch('recovery_recipe.MAX_ARCHIVE', 32), self.assertRaises(ValueError):
            native_restic.read_archive(stream)
