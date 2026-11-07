"""Restic backup keeps encrypted data and successful publication distinct."""
import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import restic_backup
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
        self.assertEqual(restic_backup.hidden_payload_paths(paths),
                         ['/snapshots/database/log', '/snapshots/parent/app/database',
                          '/snapshots/parent/app/database/log'])

    def test_cold_and_same_root_child_paths_do_not_exclude_live_data(self):
        cold = [{'source': source, 'payload': source} for source in ('/app', '/app/data', '/app/data/nested')]
        same_root = [{'source': source, 'payload': '/snapshot' + source} for source in ('/app', '/app/data')]
        self.assertEqual(restic_backup.hidden_payload_paths(cold), [])
        self.assertEqual(restic_backup.hidden_payload_paths(same_root), [])


class ResticBackupTests(unittest.TestCase):
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
            'policySha256': hashlib.sha256(restic_backup.encode(self.policy).encode()).hexdigest(),
            'archiveSha256': hashlib.sha256(self.recovery.read_bytes()).hexdigest()}))
        Path(str(self.recovery) + '.json').chmod(0o600)
        self.host = Host()
        patches = [patch('snapshots.OWNER_UID', os.getuid()),
                   patch('restic_backup.running_system', return_value=self.system),
                   patch('snapshots.machine_id', return_value='1' * 32),
                   patch('snapshots.run', self.host),
                   patch('restic_backup.require_tmpfs'),
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
        self.runner = patch.object(restic_backup.Restic, 'run', restic)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def test_restic_metadata_commands_do_not_inherit_the_callers_terminal_input(self):
        self.runner.stop()
        with patch('restic_backup.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as execute:
            restic_backup.Restic(self.policy).run(['cat', 'config'])
            self.assertEqual(execute.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(execute.call_args.kwargs['start_new_session'])

    def test_plan_does_not_create_state_or_stop_writers(self):
        with patch('backup_identity.collect', side_effect=AssertionError('dry-run account lookup')):
            result = restic_backup.backup(self.policy)
        self.assertFalse(result['applied'])
        self.assertFalse((self.root / 'state').exists())
        self.assertFalse((self.root / 'runtime').exists())
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_cold_backup_stops_writers_until_payload_and_publishes_after_recovery(self):
        result = restic_backup.backup(self.policy, apply=True)
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
            restic_backup.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_writer_recovery_failure_never_publishes_a_completion_receipt(self):
        self.host.fail_start = True
        with self.assertRaises(RuntimeError):
            restic_backup.backup(self.policy, apply=True)
        self.assertFalse(any('--stdin' in call for call in self.calls))
        self.assertTrue((self.root / 'state/pending.json').exists())

    def test_payload_failure_recovers_writers_without_publishing_completion(self):
        with patch('restic_backup.payload', side_effect=RuntimeError('simulated transport failure')):
            with self.assertRaises(RuntimeError):
                restic_backup.backup(self.policy, apply=True)
        self.assertEqual(self.host.units['web.service'], 'active')
        self.assertFalse((self.root / 'state/pending.json').exists())
        self.assertFalse(any('--stdin' in call for call in self.calls))

    def test_snapshot_lease_blocks_removal_until_encrypted_upload_finishes(self):
        self.policy['captureMethod'] = 'snapshot'
        contents = self.staged_archive()
        restic_backup.stage(self.policy, self.system, archive=contents,
                            expected_sha256=hashlib.sha256(contents).hexdigest(), apply=True)
        topology = {'roots': {'system': {**self.capture['roots']['system'],
            'filesystemUuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            'subvolumeUuid': '11111111-1111-1111-1111-111111111111'}},
            'paths': [{'source': str(self.data), 'root': 'system', 'relative': str(self.data.relative_to('/'))}]}
        original_payload = restic_backup.payload
        def payload(*args, **kwargs):
            capture = args[-1]
            with self.assertRaisesRegex(RuntimeError, 'lock'):
                snapshots.remove(self.capture, capture['snapshotId'], apply=True)
            return original_payload(*args, **kwargs)
        with patch('snapshots.inspect', side_effect=lambda *args, **kwargs: copy.deepcopy(topology)), \
                patch('snapshots.filesystem', return_value={'uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                                                           'filesystemPath': '/snapshot'}), \
                patch('snapshots.mounted_snapshot', return_value=False), \
                patch('snapshots.mountpoints', return_value=[]), \
                patch('restic_backup.payload', payload):
            result = restic_backup.backup(self.policy, apply=True)
        self.assertEqual(result['state'], 'recorded-captured')
        self.assertEqual(snapshots.inventory(self.capture)['snapshots'], [])
        with patch('snapshots.machine_id', return_value='2' * 32):
            restic_backup.validate_manifest(json.loads(self.saved['receipt']), json.loads(self.saved['manifest']))

    def test_account_lookup_failure_aborts_before_writer_shutdown(self):
        with patch('backup_identity.collect', side_effect=ValueError('account lookup failed')):
            with self.assertRaisesRegex(ValueError, 'account lookup'):
                restic_backup.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))
        self.assertFalse(self.saved)

    def test_account_evidence_is_encrypted_and_bound_to_the_manifest_version(self):
        evidence = {'architecture': 'aarch64-linux',
                    'users': [{'name': 'player', 'uid': 1000, 'gid': 100}],
                    'groups': [{'name': 'games', 'gid': 100}]}
        def collect():
            self.assertEqual(self.host.units['web.service'], 'active')
            return evidence
        with patch('backup_identity.collect', side_effect=collect):
            result = restic_backup.backup(self.policy, apply=True)
        record = json.loads(self.saved['receipt'])
        manifest = json.loads(self.saved['manifest'])
        self.assertIn('identity', manifest)
        self.assertEqual(manifest['identity'], evidence)
        self.assertNotIn('identity', record)
        self.assertNotIn('identity', result)
        restic_backup.validate_manifest(record, manifest)
        # Old captures remain readable, but cannot acquire invented identity evidence.
        version1 = {key: value for key, value in manifest.items() if key != 'identity'}
        version1['format'] = 'nixberry-restic-v1'
        restic_backup.validate_manifest({**record, 'format': 'nixberry-restic-v1'}, version1)
        for changed in (version1, {**manifest, 'identity': {**evidence, 'architecture': 'unknown'}},
                        {**manifest, 'format': 'nixberry-restic-v1'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                restic_backup.validate_manifest(record, changed)

    def test_receipt_reader_accepts_both_versions_and_checks_their_authenticated_evidence(self):
        restic_backup.backup(self.policy, apply=True)
        original_record = json.loads(self.saved['receipt'])
        original_manifest = json.loads(self.saved['manifest'])
        for version in ('nixberry-restic-v1', 'nixberry-restic-v2'):
            manifest = {**original_manifest, 'format': version}
            if version == 'nixberry-restic-v1':
                del manifest['identity']
            contents = json.dumps(manifest)
            record = {**original_record, 'format': version,
                      'manifestSha256': hashlib.sha256(contents.encode()).hexdigest()}
            def run(instance, arguments, **kwargs):
                if arguments == ['cat', 'config']:
                    return json.dumps({'id': 'a' * 64})
                if arguments == ['snapshots', self.receipt_id]:
                    return json.dumps([{'id': self.receipt_id,
                                        'tags': ['nixberry-receipt-v1', 'source:vm-demo-server']}])
                if arguments == ['dump', self.receipt_id, '/nixberry-receipt.json']:
                    return json.dumps(record)
                if arguments == ['dump', self.data_id, record['manifestPath']]:
                    return contents
                raise AssertionError(arguments)
            with self.subTest(version=version), patch.object(restic_backup.Restic, 'run', run):
                _, _, loaded = restic_backup.receipt(self.policy, self.receipt_id)
                self.assertEqual(loaded, manifest)

    def test_authenticated_manifest_rejects_changed_logical_paths_and_recovery_binding(self):
        restic_backup.backup(self.policy, apply=True)
        receipt = json.loads(self.saved['receipt'])
        manifest = json.loads(self.saved['manifest'])
        restic_backup.validate_manifest(receipt, manifest)
        for change in ({'format': 'unknown'}, {'paths': [{'source': '/etc/passwd', 'payload': '/unrelated'}]},
                       {'recovery': {'path': '/../escape', 'sha256': 'f' * 64}}, {'captureMethod': 'snapshot'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                restic_backup.validate_manifest(receipt, {**manifest, **change})

    def test_sftp_probe_fails_promptly_before_any_writer_stop(self):
        self.policy.update(repository='sftp:backup@nas.example:/backup', ssh={
            'port': 7922, 'identityFile': str(self.password), 'knownHostsFile': str(self.password)})
        with patch('restic_backup.subprocess.run', return_value=subprocess.CompletedProcess([], 255, '', '')) as execute:
            with self.assertRaisesRegex(RuntimeError, 'SFTP'):
                restic_backup.backup(self.policy, apply=True)
            self.assertEqual(execute.call_args.args[0][0], 'sftp')
            self.assertLessEqual(execute.call_args.kwargs['timeout'], 30)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_stale_recovery_archive_binding_refuses_capture_before_shutdown(self):
        with patch('restic_backup.running_system', return_value='/nix/store/' + 'e' * 32 + '-nixos-system-new'):
            with self.assertRaisesRegex(RuntimeError, 'recovery'):
                restic_backup.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_generation_change_before_capture_lock_is_rechecked_before_writer_shutdown(self):
        new = '/nix/store/' + 'e' * 32 + '-nixos-system-new'
        with patch('restic_backup.running_system', side_effect=[self.system, new]):
            with self.assertRaisesRegex(RuntimeError, 'recovery'):
                restic_backup.backup(self.policy, apply=True)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_recovery_staging_plans_and_refuses_wrong_running_generation(self):
        metadata = Path(str(self.recovery) + '.json')
        metadata.unlink()
        plan = restic_backup.stage(self.policy, self.system)
        self.assertFalse(plan['applied'])
        self.assertFalse(metadata.exists())
        with self.assertRaises(RuntimeError):
            restic_backup.stage(self.policy, '/nix/store/' + 'e' * 32 + '-nixos-system-wrong', apply=True)
        self.assertFalse(metadata.exists())
        restic_backup.stage(self.policy, self.system, apply=True)
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
            'policySha256': hashlib.sha256(restic_backup.encode(self.policy).encode()).hexdigest()}, filename)
        contents = filename.read_bytes()
        filename.unlink()
        return contents

    def test_existing_unstructured_archive_cannot_be_rebound_as_recovery_code(self):
        self.recovery.write_bytes(b'not a sealed recipe archive')
        with self.assertRaises(ValueError):
            restic_backup.stage(self.policy, self.system, apply=True)

    def test_received_archive_is_bound_atomically_only_after_hash_and_generation_checks(self):
        contents = self.staged_archive()
        checksum = hashlib.sha256(contents).hexdigest()
        before = self.recovery.read_bytes()
        with self.assertRaises(ValueError):
            restic_backup.stage(self.policy, self.system, archive=contents, expected_sha256='0' * 64, apply=True)
        self.assertEqual(self.recovery.read_bytes(), before)
        with snapshots.lock(self.capture), self.assertRaisesRegex(RuntimeError, 'lock'):
            restic_backup.stage(self.policy, self.system, archive=contents, expected_sha256=checksum, apply=True)
        self.assertEqual(self.recovery.read_bytes(), before)
        report = restic_backup.stage(self.policy, self.system, archive=contents, expected_sha256=checksum, apply=True)
        self.assertTrue(report['applied'])
        self.assertEqual(self.recovery.read_bytes(), contents)
        self.assertEqual(restic_backup.recovery(self.policy)['archiveSha256'], checksum)
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.host.commands))

    def test_received_archive_with_a_valid_hash_but_foreign_policy_cannot_replace_configuration(self):
        contents = self.staged_archive()
        checksum = hashlib.sha256(contents).hexdigest()
        before = self.recovery.read_bytes()
        different = copy.deepcopy(self.policy)
        different['sourceId'] = 'vm-demo-other'
        with self.assertRaises(ValueError):
            restic_backup.stage(different, self.system, archive=contents, expected_sha256=checksum, apply=True)
        self.assertEqual(self.recovery.read_bytes(), before)

    def test_unsafe_password_permissions_and_storage_paths_fail_closed(self):
        self.password.chmod(0o644)
        with self.assertRaises(ValueError):
            restic_backup.backup(self.policy, apply=True)
        self.password.chmod(0o600)
        for field, value in [('passwordFile', '/nix/store/plaintext-password'),
                             ('repository', str(self.data / 'recursive-backup')),
                             ('sourceId', 'invalid,tag'), ('timeoutSeconds', True)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                restic_backup.policy({**self.policy, field: value})

    def test_sftp_credentials_stay_outside_application_data(self):
        for method in ('cold', 'snapshot'):
            for key in ('identityFile', 'knownHostsFile'):
                value = copy.deepcopy(self.policy)
                value.update(captureMethod=method, repository='sftp:backup@nas.example:/backup', ssh={
                    'port': 22, 'identityFile': str(self.root / 'key'),
                    'knownHostsFile': str(self.root / 'known_hosts')})
                value['ssh'][key] = str(self.data / key)
                with self.subTest(method=method, key=key), self.assertRaises(ValueError):
                    restic_backup.policy(value)

    def test_local_repository_aliases_cannot_hide_application_data_overlap(self):
        repository = self.data / 'repository'
        repository.mkdir()
        alias = self.root / 'repository-alias'
        for method in ('cold', 'snapshot'):
            for destination in (repository, self.root):
                alias.symlink_to(destination, target_is_directory=True)
                value = {**self.policy, 'captureMethod': method, 'repository': str(alias)}
                with self.subTest(method=method, destination=str(destination)), self.assertRaises(ValueError):
                    restic_backup.policy(value)
                alias.unlink()
        storage = self.root / 'storage'
        storage.mkdir()
        alias.symlink_to(storage, target_is_directory=True)
        restic_backup.policy({**self.policy, 'repository': str(alias)})

    def test_private_file_rejects_fifo_without_waiting_for_a_writer(self):
        fifo = self.root / 'password-fifo'
        os.mkfifo(fifo, 0o600)
        script = '''import os, sys
sys.path.insert(0, sys.argv[1])
import restic_backup
try:
    restic_backup.private_file(sys.argv[2], owner_uid=os.getuid())
except ValueError:
    pass
else:
    raise SystemExit('FIFO runtime file was accepted')
'''
        try:
            result = subprocess.run([sys.executable, '-c', script, str(Path(restic_backup.__file__).parent), str(fifo)],
                                    capture_output=True, text=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail('Runtime file inspection blocked on a FIFO')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(restic_backup.private_file(self.password), b'test-only')

    def test_sftp_requires_explicit_private_identity_and_pinned_host_file(self):
        with self.assertRaises(ValueError):
            restic_backup.policy({**self.policy, 'repository': 'sftp:backup@nas.example:/backup'})
        value = copy.deepcopy(self.policy)
        value.update(repository='sftp:backup@nas.example:/backup', ssh={
            'port': 7922, 'identityFile': '/var/lib/backup/ssh-key',
            'knownHostsFile': '/var/lib/backup/known_hosts'})
        value = restic_backup.policy(value)
        with self.assertRaises(ValueError):
            restic_backup.policy({**value, 'repository': 'sftp:backup@nas.example:/backup/../other'})
        command = restic_backup.Restic(value).command()
        option = next(item for item in command if item.startswith('sftp.command='))
        for expected in ('StrictHostKeyChecking=yes', 'BatchMode=yes', 'IdentitiesOnly=yes',
                         'ForwardAgent=no', 'IdentityAgent=none', '/var/lib/backup/known_hosts'):
            self.assertIn(expected, option)

    def test_storage_pq_warning_exception_applies_to_probe_and_restic_only_when_selected(self):
        for method in ('cold', 'snapshot'):
            for warn in (None, True, False):
                value = copy.deepcopy(self.policy)
                value.update(captureMethod=method, repository='sftp:backup@nas.example:/backup', ssh={
                    'port': 7922, 'identityFile': str(self.password), 'knownHostsFile': str(self.password)})
                if warn is not None:
                    value['ssh']['warnNoPqKex'] = warn
                with self.subTest(method=method, warn=warn):
                    try:
                        restic_backup.policy(value)
                    except ValueError as failure:
                        self.fail('Explicit storage warning policy was refused: ' + str(failure))
                    reader = restic_backup.Restic(value)
                    transport = shlex.split(next(arg for arg in reader.command()
                        if arg.startswith('sftp.command=')).removeprefix('sftp.command='))
                    with patch('restic_backup.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as execute:
                        reader.transport()
                    probe = execute.call_args.args[0]
                    for command in (probe, transport):
                        self.assertEqual('WarnWeakCrypto=no-pq-kex' in command, warn is False)
                        self.assertEqual('IgnoreUnknown=WarnWeakCrypto' in command, warn is False)
                        for option in ('StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
                                       'ForwardAgent=no', 'BatchMode=yes'):
                            self.assertIn(option, command)
                        self.assertFalse(any(arg.startswith(('KexAlgorithms=', 'LogLevel=', 'WarnWeakCrypto=no'))
                                             and arg != 'WarnWeakCrypto=no-pq-kex' for arg in command))
                    parsed = subprocess.run(['ssh', '-G', *transport[1:]], text=True, capture_output=True, check=True)
                    configured = dict(line.split(' ', 1) for line in parsed.stdout.splitlines())
                    self.assertIn(configured['warnweakcrypto'], ('no', 'no-pq-kex') if warn is False else ('yes',))

    def test_storage_warning_policy_rejects_non_booleans_and_unknown_ssh_options(self):
        for change in ({'warnNoPqKex': 0}, {'warnNoPqKex': 'false'}, {'warnNoPqKex': None},
                       {'StrictHostKeyChecking': 'no'}):
            value = copy.deepcopy(self.policy)
            value.update(repository='sftp:backup@nas.example:/backup', ssh={
                'port': 7922, 'identityFile': str(self.password), 'knownHostsFile': str(self.password), **change})
            with self.subTest(change=change), self.assertRaises(ValueError):
                restic_backup.policy(value)


class ResticPolicySystemTests(unittest.TestCase):
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
                self.assertEqual(set(value['ssh']), {'port', 'identityFile', 'knownHostsFile'}, name)
            exception = ('let f=builtins.getFlake ' + nix('path:' + str(root))
                + '; in builtins.mapAttrs (_: system: (system.extendModules { modules = [ '
                + '{ nixberry.backup.restic.ssh.warnNoPqKex = false; } ]; })'
                + '.config.system.build.nixberryBackupPolicy.ssh) f.nixberrySystems')
            result = subprocess.run(['nix', 'eval', '--json', '--impure', '--no-write-lock-file', '--expr', exception],
                                    capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])
            for name, ssh in json.loads(result.stdout).items():
                self.assertEqual(ssh, {**values[name]['ssh'], 'warnNoPqKex': False}, name)
            from capture_controller import projection
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
            restic_backup.read_archive(stream, timeout=0.02)

    def test_transfer_size_is_bounded_before_archive_parsing(self):
        read_descriptor, write_descriptor = os.pipe()
        os.write(write_descriptor, b'x' * 33)
        os.close(write_descriptor)
        with os.fdopen(read_descriptor, 'rb') as stream, patch('recovery_recipe.MAX_ARCHIVE', 32), self.assertRaises(ValueError):
            restic_backup.read_archive(stream)
