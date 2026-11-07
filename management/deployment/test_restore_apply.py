"""A restore transaction owns empty paths until durable publication completes."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import restore_apply
import snapshots
from test_snapshots import settings

SYSTEM = '/nix/store/' + 'a' * 32 + '-nixos-system-target'
MACHINE = 'b' * 32


class RestoreApplyFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / 'app'
        self.state = self.root / 'restore'
        self.value = {'version': 1, 'architecture': 'x86_64-linux', 'stateDirectory': str(self.state),
                      'automaticUnits': [], 'applications': {'web': {'paths': [str(self.app)], 'units': ['web.service']}}}
        self.capture = settings(self.root)
        self.capture['applications']['web']['paths'] = [str(self.app)]
        self.reader = SimpleNamespace(settings={'sourceId': 'vm-demo-source'})
        self.units = []
        def stage(reader, identifier, destination, identity):
            destination = Path(destination)
            destination.mkdir(mode=0o700)
            raw = destination / 'data/payload'
            raw.mkdir(parents=True)
            (raw / 'example').write_text('restored data')
            os.link(raw / 'example', raw / 'hardlink')
            (raw / 'symlink').symlink_to('example')
            report = {'version': 1, 'state': 'staged-verified', 'sourceId': 'vm-demo-source',
                      'snapshotId': identifier, 'payloadId': 'c' * 64, 'manifestSha256': 'd' * 64,
                      'sourceMachineId': 'e' * 32, 'architecture': 'x86_64-linux',
                      'paths': [{'source': str(self.app), 'staged': str(raw)}]}
            (destination / 'stage.json').write_text(json.dumps(report))
            (destination / 'stage.json').chmod(0o600)
            return report
        def status(value, **kwargs):
            return {'completed': False, 'units': {'web.service': 'inactive'},
                    'systemPath': SYSTEM, 'machineId': MACHINE}
        for item in (patch('snapshots.OWNER_UID', os.getuid()), patch('restore_apply.os.geteuid', return_value=0),
                     patch('snapshots.running_system', return_value=SYSTEM),
                     patch('snapshots.machine_id', return_value=MACHINE),
                     patch('restore_apply.layout', return_value=[{'target': '/', 'uuid': 'f' * 32}]),
                     patch('restore_apply.restore_target.status', side_effect=status),
                     patch('restore_apply.backup_identity.collect', return_value={}),
                     patch('restore_apply.restore_payload.stage', side_effect=stage),
                     patch('restore_apply.start_units', side_effect=lambda value: self.units.append('started'))):
            item.start()
            self.addCleanup(item.stop)

    def run_restore(self, **kwargs):
        return restore_apply.restore(self.value, self.capture, self.reader, '1' * 64,
                                     SYSTEM, MACHINE, **kwargs)


class RestoreApplyTests(RestoreApplyFixture):
    def test_completed_restore_publishes_hardlinks_before_releasing_units(self):
        result = self.run_restore(apply=True)
        self.assertEqual(result['state'], 'recorded-restored')
        self.assertEqual((self.app / 'example').read_text(), 'restored data')
        self.assertEqual((self.app / 'example').stat().st_ino, (self.app / 'hardlink').stat().st_ino)
        self.assertEqual(os.readlink(self.app / 'symlink'), 'example')
        self.assertEqual(self.units, ['started'])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.state / 'restore-pending.json').exists())
        marker = restore_apply.restore_target.marker_path(self.value)
        self.assertEqual(json.loads(marker.read_text())['snapshotId'], '1' * 64)

    def test_whiteout_hash_checks_metadata_without_opening_the_device(self):
        self.app.mkdir()
        whiteout = self.app / 'removed'
        os.mknod(whiteout, stat.S_IFCHR | 0o640, os.makedev(0, 0))
        original_open = os.open
        def open_regular_or_directory(path, *args, **kwargs):
            if Path(path) == whiteout:
                self.fail('Whiteout hashing must not open device nodes')
            return original_open(path, *args, **kwargs)
        with patch('restore_apply.os.open', side_effect=open_regular_or_directory):
            original = restore_apply.tree_hash(Path('/'), [self.app], durable=True)
            os.chmod(whiteout, 0o600)
            self.assertNotEqual(restore_apply.tree_hash(Path('/'), [self.app]), original)



    def test_service_home_without_ssh_restores_application_data(self):
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]):
            result = self.run_restore(apply=True)
        self.assertEqual(result['state'], 'recorded-restored')
        self.assertEqual((self.app / 'example').read_text(), 'restored data')
        self.assertFalse((self.app / '.ssh').exists())
        self.assertEqual(self.units, ['started'])

    def test_service_home_payload_ssh_objects_are_rejected_before_publication(self):
        original = restore_apply.restore_payload.stage.side_effect
        for kind in ('directory', 'file', 'symlink'):
            with self.subTest(kind=kind):
                def stage(*args):
                    report = original(*args)
                    ssh = Path(report['paths'][0]['staged']) / '.ssh'
                    if kind == 'directory':
                        ssh.mkdir()
                        (ssh / 'authorized_keys').write_text('incoming SSH identity')
                    elif kind == 'file':
                        ssh.write_text('incoming SSH object')
                    else:
                        ssh.symlink_to(self.root / 'missing-ssh-target')
                    return report
                with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]), \
                        patch('restore_apply.restore_payload.stage', side_effect=stage), self.assertRaises(ValueError):
                    self.run_restore(apply=True)
                self.assertFalse(self.app.exists())
                self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())
                self.assertEqual(self.units, [])

    def test_separately_captured_service_home_ssh_is_rejected(self):
        original = restore_apply.restore_payload.stage.side_effect
        def stage(*args):
            report = original(*args)
            ssh = Path(args[2]) / 'data/ssh'
            ssh.mkdir()
            (ssh / 'authorized_keys').write_text('separately captured SSH identity')
            report['paths'].append({'source': str(self.app / '.ssh'), 'staged': str(ssh)})
            return report
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]), \
                patch('restore_apply.restore_payload.stage', side_effect=stage), self.assertRaises(ValueError):
            self.run_restore(apply=True)
        self.assertFalse(self.app.exists())
        self.assertEqual(self.units, [])

    def test_existing_service_home_ssh_is_preserved(self):
        self.app.mkdir()
        ssh = self.app / '.ssh'
        ssh.mkdir()
        (ssh / 'authorized_keys').write_text('destination SSH identity')
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]), \
                self.assertRaises(ValueError):
            self.run_restore(apply=True)
        self.assertEqual((ssh / 'authorized_keys').read_text(), 'destination SSH identity')
        self.assertFalse((self.app / 'example').exists())
        self.assertFalse(self.state.exists())
        self.assertEqual(self.units, [])

    def test_copying_retry_rejects_new_live_service_home_ssh(self):
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]):
            with patch('restore_apply.transfer', side_effect=RuntimeError('copy interrupted')), self.assertRaises(RuntimeError):
                self.run_restore(apply=True)
            self.app.mkdir()
            ssh = self.app / '.ssh'
            ssh.symlink_to(self.root / 'missing-ssh-target')
            with self.assertRaises(ValueError):
                self.run_restore(apply=True)
        self.assertTrue(ssh.is_symlink())
        self.assertEqual(os.readlink(ssh), str(self.root / 'missing-ssh-target'))
        self.assertFalse((self.app / 'example').exists())
        self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())
        self.assertEqual(self.units, [])

    def test_copying_retry_rejects_injected_private_service_home_ssh(self):
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]):
            with patch('restore_apply.transfer', side_effect=RuntimeError('copy interrupted')), self.assertRaises(RuntimeError):
                self.run_restore(apply=True)
            pending = json.loads((self.state / 'restore-pending.json').read_text())
            ssh = Path(pending['directory']) / 'view' / str(self.app).removeprefix('/') / '.ssh'
            ssh.write_text('injected SSH object')
            with self.assertRaises(ValueError):
                self.run_restore(apply=True)
        self.assertFalse(self.app.exists())
        self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())
        self.assertEqual(self.units, [])

    def test_nested_service_home_symlink_is_not_followed(self):
        original = restore_apply.restore_payload.stage.side_effect
        home = self.app / 'service'
        def stage(*args):
            report = original(*args)
            (Path(report['paths'][0]['staged']) / 'service').symlink_to(self.root / 'missing-home')
            return report
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(home))]), \
                patch('restore_apply.restore_payload.stage', side_effect=stage), self.assertRaises(ValueError):
            self.run_restore(apply=True)
        self.assertFalse(self.app.exists())
        self.assertEqual(self.units, [])

    def test_direct_service_home_ssh_paths_are_rejected_even_when_absent(self):
        with patch('restore_apply.pwd.getpwall', return_value=[SimpleNamespace(pw_dir=str(self.app))]):
            for path in (self.app / '.ssh', self.app / '.ssh/authorized_keys'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    restore_apply.protect([path], self.value['stateDirectory'])

    def test_partial_staging_report_is_replaced_on_exact_retry(self):
        original = restore_apply.restore_payload.stage.side_effect
        calls = []
        def interrupted(*args):
            report = original(*args)
            calls.append(str(args[2]))
            if len(calls) == 1:
                directory = Path(args[2])
                (directory / 'INCOMPLETE').write_text('interrupted staging')
                (directory / 'stage.json').write_text('{')
                raise InterruptedError('simulated interrupted report')
            return report
        with patch('restore_apply.restore_payload.stage', side_effect=interrupted):
            with self.assertRaises(InterruptedError):
                self.run_restore(apply=True)
            self.assertFalse(self.app.exists())
            self.assertEqual(self.units, [])
            result = self.run_restore(apply=True)
        self.assertEqual(result['state'], 'recorded-restored')
        self.assertEqual(calls, [calls[0], calls[0]])
        self.assertEqual((self.app / 'example').read_text(), 'restored data')

    def test_report_without_copying_journal_does_not_allow_unverified_stage_reuse(self):
        original = restore_apply.restore_payload.stage.side_effect
        calls = []
        def stage(*args):
            report = original(*args)
            calls.append(str(args[2]))
            return report
        with patch('restore_apply.restore_payload.stage', side_effect=stage):
            with patch('restore_apply.canonical', side_effect=InterruptedError('before durable copying journal')):
                with self.assertRaises(InterruptedError):
                    self.run_restore(apply=True)
            (Path(calls[0]) / 'data/payload/example').write_text('unverified changed private stage')
            self.assertFalse(self.app.exists())
            self.run_restore(apply=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual((self.app / 'example').read_text(), 'restored data')

    def test_separately_captured_nested_paths_publish_into_one_logical_tree(self):
        original = restore_apply.restore_payload.stage.side_effect
        def nested(*args):
            report = original(*args)
            child = Path(args[2]) / 'data/database'
            child.mkdir()
            (child / 'current').write_text('live child')
            report['paths'].append({'source': str(self.app / 'database[1]?*\\raw name'), 'staged': str(child)})
            return report
        with patch('restore_apply.restore_payload.stage', side_effect=nested):
            self.run_restore(apply=True)
        self.assertEqual((self.app / 'database[1]?*\\raw name/current').read_text(), 'live child')

    def test_cross_filesystem_publication_copies_and_preserves_target_hardlinks(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as memory:
            self.state = Path(memory) / 'restore'
            self.value['stateDirectory'] = str(self.state)
            self.run_restore(apply=True)
            self.assertEqual((self.app / 'example').read_text(), 'restored data')
            self.assertEqual((self.app / 'example').stat().st_ino, (self.app / 'hardlink').stat().st_ino)

    def test_cross_filesystem_transfer_keeps_cross_root_hardlinks_and_symlinks_without_link_dest(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as memory:
            paths = [Path(memory) / 'first', Path(memory) / 'second']
            view = self.root / 'view'
            sources = [view / str(path).removeprefix('/') for path in paths]
            for source in sources:
                source.mkdir(parents=True)
            (sources[0] / 'example').write_text('cross-root data')
            os.link(sources[0] / 'example', sources[1] / 'hardlink')
            (sources[1] / 'symlink').symlink_to('../first/example')
            original_run = snapshots.run
            def run(arguments, **kwargs):
                if arguments[0] == 'rsync':
                    self.assertFalse(any(argument.startswith('--link-dest=') for argument in arguments))
                return original_run(arguments, **kwargs)
            expected = restore_apply.tree_hash(view, paths)
            restore_apply.compatible_links(view, paths)
            with patch('restore_apply.snapshots.run', side_effect=run):
                restore_apply.transfer(view, paths, self.root)
                restore_apply.transfer(view, paths, self.root, check=True)
            self.assertEqual(restore_apply.tree_hash(Path('/'), paths), expected)
            self.assertEqual((paths[0] / 'example').stat().st_ino, (paths[1] / 'hardlink').stat().st_ino)
            self.assertEqual(os.readlink(paths[1] / 'symlink'), '../first/example')

    def test_same_mount_publication_reuses_verified_regular_files(self):
        original = restore_apply.restore_payload.stage.side_effect
        staged = []
        def stage(*args):
            report = original(*args)
            staged.append((Path(report['paths'][0]['staged']) / 'example').stat().st_ino)
            return report
        with patch('restore_apply.restore_payload.stage', side_effect=stage):
            self.run_restore(apply=True)
        self.assertEqual((self.app / 'example').stat().st_ino, staged[0])

    def test_unmounted_nested_subvolume_device_disables_link_dest(self):
        view = self.root / 'view'
        view.mkdir()
        nested = self.app / 'nested-subvolume'
        nested.mkdir(parents=True)
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            details = original(path, *args, **kwargs)
            if path == nested:
                fields = list(details)
                fields[2] = details.st_dev + 1
                return os.stat_result(fields)
            return details
        with patch.object(Path, 'lstat', lstat):
            self.assertFalse(restore_apply.link_dest_compatible(view, [self.app]))


    def test_source_hardlinks_cannot_be_split_across_destination_filesystems(self):
        view = self.root / 'view'
        first = self.root / 'first'
        second = Path('/dev/shm/nixberry-uncreated-test-target/second')
        a = view / str(first).removeprefix('/')
        b = view / str(second).removeprefix('/')
        a.parent.mkdir(parents=True)
        b.parent.mkdir(parents=True)
        a.write_text('hardlink data')
        os.link(a, b)
        with self.assertRaises(ValueError):
            restore_apply.compatible_links(view, [first, second])

    def test_wrong_identity_and_nonempty_destination_never_publish_or_create_completion(self):
        self.app.mkdir()
        (self.app / 'keep').write_text('original')
        with self.assertRaises(ValueError):
            self.run_restore(apply=True)
        self.assertEqual((self.app / 'keep').read_text(), 'original')
        self.assertEqual(self.units, [])
        self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())
        with patch('snapshots.machine_id', return_value='f' * 32), self.assertRaises(ValueError):
            self.run_restore(apply=True)

    def test_copy_interruption_resumes_only_the_reserved_transaction(self):
        real_copy = restore_apply.transfer
        def interrupted(*args, **kwargs):
            real_copy(*args, **kwargs)
            raise RuntimeError('interrupted after copying')
        with patch('restore_apply.transfer', side_effect=interrupted), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        self.assertTrue((self.app / 'example').exists())
        self.assertTrue((self.state / 'restore-pending.json').exists())
        self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())
        self.assertEqual(self.units, [])
        self.assertEqual(self.run_restore(apply=True)['state'], 'recorded-restored')

    def test_unknown_partial_destination_file_is_retained_and_completion_is_refused(self):
        real_copy = restore_apply.transfer
        def interrupted(*args, **kwargs):
            real_copy(*args, **kwargs)
            (self.app / 'unexpected').write_text('do not delete')
            raise RuntimeError('interrupted')
        with patch('restore_apply.transfer', side_effect=interrupted), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        with self.assertRaises(ValueError):
            self.run_restore(apply=True)
        self.assertEqual((self.app / 'unexpected').read_text(), 'do not delete')
        self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())

    def test_completed_request_retries_activation_without_overwriting_changed_application_data(self):
        self.run_restore(apply=True)
        (self.app / 'example').write_text('new application state')
        self.run_restore(apply=True)
        self.assertEqual((self.app / 'example').read_text(), 'new application state')
        self.assertEqual(self.units, ['started', 'started'])

    def test_crash_after_durable_marker_finishes_journal_cleanup_on_retry(self):
        original = snapshots.write_record
        marker = restore_apply.restore_target.marker_path(self.value)
        def write(path, value):
            original(path, value)
            if Path(path) == marker:
                raise RuntimeError('crash after marker')
        with patch('snapshots.write_record', side_effect=write), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        self.assertTrue(marker.exists())
        self.assertTrue((self.state / 'restore-pending.json').exists())
        self.run_restore(apply=True)
        self.assertFalse((self.state / 'restore-pending.json').exists())

    def test_only_unpublished_staging_can_be_aborted_without_removing_application_data(self):
        with patch('restore_apply.restore_payload.stage', side_effect=RuntimeError('invalid owner')), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        result = restore_apply.abort(self.value, self.capture, SYSTEM, MACHINE, apply=True)
        self.assertEqual(result['state'], 'aborted-unpublished')
        self.assertFalse((self.state / 'restore-pending.json').exists())
        with patch('restore_apply.transfer', side_effect=RuntimeError('copy interrupted')), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        with self.assertRaises(ValueError):
            restore_apply.abort(self.value, self.capture, SYSTEM, MACHINE, apply=True)
        self.assertTrue((self.state / 'restore-pending.json').exists())

    def test_reserved_os_identity_paths_are_rejected(self):
        for path in ('/etc', '/etc/passwd', '/etc/ssh', '/root/.ssh', '/nix', '/var/lib/nixos'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                restore_apply.protect([path], self.value['stateDirectory'])

    def test_capture_lock_prevents_restore_and_restore_journal_prevents_foreign_resume(self):
        with snapshots.lock(self.capture), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        with patch('restore_apply.transfer', side_effect=RuntimeError('interrupted')), self.assertRaises(RuntimeError):
            self.run_restore(apply=True)
        self.reader.settings['sourceId'] = 'foreign-source'
        with self.assertRaises(ValueError):
            self.run_restore(apply=True)
