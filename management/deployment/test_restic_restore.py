"""Global Restic restoration preserves source independence and target verification."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cli import main
import test_capture_controller
import restic_restore
import restore_access
import restic_access


class ResticRestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.password = self.root / 'password'
        self.password.write_text('test-only-password')
        self.password.chmod(0o600)
        self.context = {'version': 1, 'target': 'vm-demo-lost-source', 'sourceId': 'lost-source',
                        'repository': '/backup/test', 'repositoryId': 'a' * 64, 'passwordFile': str(self.password),
                        'runtimeDirectory': str(self.root / 'runtime'), 'ssh': None, 'timeoutSeconds': 120}
        self.access = self.root / 'access.json'
        self.access.write_text(json.dumps(self.context))
        self.access.chmod(0o600)
        self.target = test_capture_controller.CaptureDispatchTests().target()
        self.system = '/nix/store/' + 'b' * 32 + '-nixos-system-target'
        self.selected = {'restore': {'test': 'restore'}, 'capture': {'test': 'capture'}, 'systemPath': self.system}
        self.identity = {'systemPath': self.system, 'machineId': 'd' * 32,
                         'ready': True, 'pending': False, 'completed': False}
        for item in (patch('restic_restore.freeze_recipe', return_value='/frozen'),
                     patch('restic_restore.projection', return_value=self.selected)):
            item.start()
            self.addCleanup(item.stop)

    def test_default_plan_does_not_contact_source_or_destination_or_read_secret_files(self):
        with patch('restic_restore.status', side_effect=AssertionError('network on plan')), \
                patch('restore_access.pack', side_effect=AssertionError('secret read on plan')):
            result = restic_restore.run('/recipe', self.target, 'rpi-demo', 'vm-demo-lost-source',
                                        self.access, 'c' * 64)
        self.assertFalse(result['applied'])
        self.assertEqual(result['readiness'], 'not-checked')

    def test_global_cli_dispatches_without_an_original_source_declaration(self):
        registry = self.root / 'registry.json'
        registry.write_text(json.dumps({'rpi-demo': self.target}))
        output = io.StringIO()
        with patch.dict(os.environ, {'NIXBERRY_DEPLOYMENT': str(registry)}), \
                patch.dict(main.__globals__, {'run_restic_restore': lambda *args, **kwargs:
                    {'target': args[2], 'source': args[3], 'applied': False}}), contextlib.redirect_stdout(output):
            self.assertEqual(main(['restore', 'rpi-demo', '--from', 'vm-demo-lost-source',
                '--backup-access', str(self.access), '--snapshot', 'c' * 64, '--json']), 0)
        self.assertEqual(json.loads(output.getvalue())['source'], 'vm-demo-lost-source')

    def test_target_policies_and_machine_identity_are_checked_before_secrets_are_sent(self):
        replies = []
        def remote(command, script, timeout, **kwargs):
            if not kwargs:
                return {'restore': self.selected['restore'], 'capture': self.selected['capture'],
                        'identity': self.identity}
            replies.append((script, kwargs['contents']))
            return {'applied': True, 'state': 'recorded-restored', 'machineId': 'd' * 32,
                    'systemPath': self.system, 'sourceId': 'lost-source', 'snapshotId': 'c' * 64}
        with patch('restic_restore.status', return_value={}), \
                patch('restic_restore.preflight', return_value=self.system), \
                patch('restic_restore.connection', return_value=['ssh', 'root@test-only']), \
                patch('restic_restore.remote', side_effect=remote):
            result = restic_restore.run('/recipe', self.target, 'rpi-demo', 'vm-demo-lost-source',
                                        self.access, 'c' * 64, apply=True)
        self.assertEqual(result['state'], 'recorded-restored')
        self.assertNotIn('test-only-password', replies[0][0])
        bundle = json.loads(replies[0][1])
        self.assertEqual(bundle['context']['repositoryId'], 'a' * 64)
        self.assertIn('--machine-id', replies[0][0])
        self.assertIn('--unit=nixberry-restore', replies[0][0])
        self.assertIn('--property=KillMode=control-group', replies[0][0])
        with patch('restic_restore.status', return_value={}), \
                patch('restic_restore.preflight', return_value=self.system), \
                patch('restic_restore.connection', return_value=['ssh', 'root@test-only']), \
                patch('restic_restore.remote', return_value={'restore': self.selected['restore'],
                    'capture': self.selected['capture'], 'identity': {**self.identity, 'machineId': 'invalid'}}), \
                patch('restore_access.pack', side_effect=AssertionError('secrets before identity')):
            with self.assertRaises(RuntimeError):
                restic_restore.run('/recipe', self.target, 'rpi-demo', 'vm-demo-lost-source',
                                   self.access, 'c' * 64, apply=True)

    def test_installed_policy_inspection_uses_the_packaged_helper_without_global_python(self):
        binaries = self.root / 'guest-bin'
        binaries.mkdir()
        wrappers = self.root / 'guest-wrappers'
        wrappers.mkdir()
        policies = self.root / 'guest-policies'
        policies.mkdir()
        shell = shutil.which('bash')
        self.assertIsNotNone(shell)
        (binaries / 'cat').symlink_to(shutil.which('cat'))
        self.assertIsNone(shutil.which('python3', path=str(binaries)))
        self.selected['restore']['test'] = 'quoted "value"\n$(unexpected-command)'
        self.selected['capture']['test'] = 'capture `unexpected-command`'
        for name in ('restore', 'capture'):
            (policies / (name + '.json')).write_text(json.dumps(self.selected[name], indent=2))
        helper = binaries / 'nixberry-restore-target'
        helper.write_text('#!' + shell + '\nset -eu\n'
            + 'test "$#" = 4 && test "$1" = status && test "$2" = --system && test "$3" = '
            + shlex.quote(self.system) + ' && test "$4" = --json\n'
            + 'if command -v python3 >/dev/null 2>&1; then exit 91; fi\n'
            + "printf '%s\\n' " + shlex.quote(json.dumps(self.identity)) + '\n')
        helper.chmod(0o700)
        execute = subprocess.run
        pack = restore_access.pack
        events = []
        def command(arguments, **kwargs):
            if kwargs['input'] is not None:
                events.append('restore')
                report = {'applied': True, 'state': 'recorded-restored', 'machineId': 'd' * 32,
                          'systemPath': self.system, 'sourceId': 'lost-source', 'snapshotId': 'c' * 64}
                return subprocess.CompletedProcess(arguments, 0, json.dumps(report).encode(), b'')
            events.append('inspect')
            script = arguments[-1].replace('/run/current-system/sw/bin', str(binaries)) \
                .replace('/run/wrappers/bin', str(wrappers)).replace('/etc/nixberry', str(policies))
            return execute([shell, '-c', script], capture_output=True, text=True,
                           env={**os.environ, 'PATH': str(binaries)}, timeout=5)
        def secrets(context):
            events.append('secrets')
            return pack(context)
        with patch('restic_restore.status', return_value={}), \
                patch('restic_restore.preflight', return_value=self.system), \
                patch('restic_restore.connection', return_value=['ssh', 'root@test-only']), \
                patch('restic_restore.subprocess.run', side_effect=command), \
                patch('restore_access.pack', side_effect=secrets):
            result = restic_restore.run('/recipe', self.target, 'rpi-demo', 'vm-demo-lost-source',
                                        self.access, 'c' * 64, apply=True)
        self.assertEqual(result['state'], 'recorded-restored')
        self.assertEqual(events, ['inspect', 'secrets', 'restore'])

    def test_received_credentials_are_private_bounded_and_removed_after_failure(self):
        original = restore_access.snapshots.secure_directory
        parent = self.root / 'received'
        parent.mkdir(mode=0o700)
        def secure(path, **kwargs):
            return parent if str(path) == '/run/nixberry-restore-access' else original(path, **kwargs)
        bundle = restore_access.pack(self.context)
        with patch('snapshots.OWNER_UID', os.getuid()), patch('restore_access.snapshots.secure_directory', side_effect=secure), \
                patch('restic_access.restic_backup.require_tmpfs'):
            with self.assertRaisesRegex(RuntimeError, 'simulated'), restore_access.receive(bundle, 'vm-demo-lost-source') as value:
                self.assertEqual(Path(value['passwordFile']).read_text(), 'test-only-password')
                self.assertEqual(Path(value['passwordFile']).stat().st_mode & 0o777, 0o600)
                raise RuntimeError('simulated')
        self.assertEqual(list(parent.iterdir()), [])
        with self.assertRaises(ValueError):
            with restore_access.receive(b'x' * (restore_access.LIMIT + 1), 'vm-demo-lost-source'):
                pass
