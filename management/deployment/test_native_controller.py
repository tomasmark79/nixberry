"""Shared capture commands preserve configuration ownership and verified access."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cli import main, operations


class NativeDispatchTests(unittest.TestCase):
    def target(self, backend='rpi'):
        return {'backend': backend, 'host': 'rpi-demo', 'configuration': 'server', 'commands': {},
                'spec': {'hostName': 'rpi-demo', 'hardwareSerial': '0000000000000001'},
                'capabilities': {'build': True, 'export': True, 'system': True, 'hypervisor': False}}

    def test_native_capture_is_available_without_a_duplicate_target_backup_catalogue(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware', 'rpi'):
            with self.subTest(backend=backend):
                available = operations(self.target(backend))
                for action in ('snapshot', 'snapshots', 'snapshot-remove', 'snapshot-resume', 'backup', 'backup-stage'):
                    self.assertIn(action, available)

    def test_snapshot_plan_dispatches_without_apply_and_keeps_json_on_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / 'registry.json'
            registry.write_text(json.dumps({'rpi-demo': self.target()}))
            output, errors = io.StringIO(), io.StringIO()
            calls = []
            def run(recipe, target, action, name, **kwargs):
                calls.append((recipe, action, name, kwargs))
                return {'action': action, 'target': name, 'applied': False, 'readiness': 'not-checked'}
            with patch.dict(os.environ, {'NIXBERRY_DEPLOYMENT': str(registry), 'NIXBERRY_RECIPE': directory}), \
                    patch.dict(main.__globals__, {'run_native': run}), \
                    patch('subprocess.run', side_effect=AssertionError('Dispatch must not invoke deployment tools')), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                try:
                    code = main(['snapshot', 'rpi-demo', '--json'])
                except SystemExit as failure:
                    code = failure.code
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual(json.loads(output.getvalue())['readiness'], 'not-checked')
            self.assertFalse(calls[0][3]['apply'])
            self.assertEqual(errors.getvalue(), '')

    def test_new_ssh_override_does_not_expand_legacy_provider_authority(self):
        from test_backups import settings
        target = self.target('libvirt')
        target.update(offline=True, backup=settings())
        target['spec']['name'] = 'vm-demo-source'
        calls = []
        def provider(*args, **kwargs):
            calls.append(args)
            return {'applied': False}
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / 'registry.json'
            registry.write_text(json.dumps({'vm-demo-source': target}))
            with patch.dict(os.environ, {'NIXBERRY_DEPLOYMENT': str(registry)}), \
                    patch.dict(main.__globals__, {'run_backup': provider}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = main(['backup', 'vm-demo-source', '--ssh', 'root@192.0.2.123:22', '--json'])
                except SystemExit as failure:
                    code = failure.code
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])


class NativeSourceTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.find_spec('native_controller')
        self.assertIsNotNone(spec, 'Shared native source dispatch is not implemented')
        import native_controller
        self.controller = native_controller
        self.system = '/nix/store/' + 'd' * 32 + '-nixos-system-rpi-demo'
        self.capture = {'version': 1, 'applications': {'web': {'paths': ['/var/lib/web'], 'stopUnits': ['web.service']}},
                        'roots': {'system': {'path': '/', 'snapshotDirectory': '/.snapshots/nixberry'}},
                        'stateDirectory': '/var/lib/nixberry-capture', 'minFreeBytes': 1024, 'timeoutSeconds': 120}
        self.policy = {'enabled': True, 'captureMethod': 'snapshot', 'capture': self.capture,
                       'restic': {'timeoutSeconds': 600}, 'systemPath': self.system}
        self.target = NativeDispatchTests().target()
        self.patches = [patch('native_controller.freeze_recipe', return_value='/frozen'),
                        patch('native_controller.projection', return_value=self.policy)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_native_stage_plan_prepares_reviewable_archive_without_source_access(self):
        from native_controller import run
        with patch('native_controller.prepare_archive', create=True, return_value=(b'test archive', {'files': {'flake.nix': 'a'*64}})), \
                patch('native_controller.status', side_effect=AssertionError('Stage plan must not contact source')):
            result = run('/recipe', self.target, 'backup-stage', 'rpi-demo')
        self.assertFalse(result['applied'])
        self.assertEqual(result['archiveFiles'], ['flake.nix'])
        self.assertEqual(result['archiveBytes'], 12)

    def test_default_plan_never_contacts_source_or_stops_writers(self):
        with patch('native_controller.status', side_effect=AssertionError('Plan must not contact the source')):
            result = self.controller.run('/recipe', self.target, 'snapshot', 'rpi-demo')
        self.assertFalse(result['applied'])
        self.assertEqual(result['readiness'], 'not-checked')
        self.assertEqual(result['policy']['applications']['web']['stopUnits'], ['web.service'])

    def test_wrong_running_generation_cannot_invoke_capture(self):
        with patch('native_controller.status', return_value={'state': 'running'}), \
                patch('native_controller.preflight', return_value='/nix/store/' + 'e' * 32 + '-nixos-system-other'), \
                patch('native_controller.subprocess.run', side_effect=AssertionError('No remote command after mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'running system'):
                self.controller.run('/recipe', self.target, 'snapshot', 'rpi-demo', apply=True)

    def test_apply_passes_only_installed_policy_and_explicit_action_after_identity_check(self):
        calls = []
        def remote(command, **kwargs):
            import subprocess
            calls.append((command, kwargs))
            if command[-1].endswith('cat /etc/nixberry/capture.json'):
                return subprocess.CompletedProcess(command, 0, json.dumps(self.capture), '')
            self.assertIn('nixberry-capture snapshot --json --apply', command[-1])
            self.assertIn('--system ' + self.system, command[-1])
            self.assertIn(self.system, command[-1])
            return subprocess.CompletedProcess(command, 0, json.dumps({'applied': True, 'snapshotId': 'a' * 32, 'state': 'ready'}), '')
        with patch('native_controller.status', return_value={'state': 'running'}), \
                patch('native_controller.preflight', return_value=self.system), \
                patch('native_controller.connection', return_value=['ssh', '--', 'root@rpi-demo']), \
                patch('native_controller.subprocess.run', side_effect=remote):
            result = self.controller.run('/recipe', self.target, 'snapshot', 'rpi-demo', apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(result['snapshotId'], 'a' * 32)
        self.assertEqual(len(calls), 2)

    def test_staging_sends_binary_archive_after_installed_backup_policy_check(self):
        import hashlib
        import subprocess
        contents = b'archive bytes\x00\r\n'
        checksum = hashlib.sha256(contents).hexdigest()
        calls = []
        def execute(command, **kwargs):
            calls.append(command)
            if command[-1].endswith('cat /etc/nixberry/backup.json'):
                self.assertIsNone(kwargs['input'])
                return subprocess.CompletedProcess(command, 0, json.dumps(self.policy['restic']), '')
            self.assertIn('nixberry-restic backup-stage --json --apply', command[-1])
            self.assertIn('--receive-sha256 ' + checksum, command[-1])
            self.assertIn('--system ' + self.system, command[-1])
            self.assertEqual(kwargs['input'], contents)
            self.assertFalse(kwargs['text'])
            return subprocess.CompletedProcess(command, 0, b'{"applied":true}', b'')
        with patch('native_controller.prepare_archive', return_value=(contents, {'files': {}})), \
                patch('native_controller.status', return_value={'state':'running'}), \
                patch('native_controller.preflight', return_value=self.system), \
                patch('native_controller.connection', return_value=['ssh', '--', 'root@rpi-demo']), \
                patch('native_controller.subprocess.run', side_effect=execute):
            result = self.controller.run('/recipe', self.target, 'backup-stage', 'rpi-demo', apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(len(calls), 2)

    def test_remote_policy_mismatch_cannot_capture_or_recover_writers(self):
        import subprocess
        calls = []
        def remote(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{}', '')
        with patch('native_controller.status', return_value={'state': 'running'}), \
                patch('native_controller.preflight', return_value=self.system), \
                patch('native_controller.connection', return_value=['ssh', '--', 'root@rpi-demo']), \
                patch('native_controller.subprocess.run', side_effect=remote):
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                self.controller.run('/recipe', self.target, 'snapshot-resume', 'rpi-demo', apply=True)
        self.assertEqual(len(calls), 1)

    def test_local_snapshot_ids_and_read_only_actions_fail_before_source_access(self):
        for action, args in [('snapshot-remove', {}), ('snapshot-remove', {'snapshot': 'a' * 64}),
                             ('snapshots', {'apply': True}), ('snapshot', {'snapshot': 'a' * 32})]:
            with self.subTest(action=action, args=args), self.assertRaises(ValueError):
                self.controller.run('/recipe', self.target, action, 'rpi-demo', **args)


class NativeProjectionErrors(unittest.TestCase):
    def test_nix_failure_forwards_diagnostics_and_does_not_return_a_policy(self):
        import subprocess
        from native_controller import projection
        errors = io.StringIO()
        with patch('native_controller.subprocess.run', return_value=subprocess.CompletedProcess([], 1, 'null', 'Nix fixture failure\n')), \
                contextlib.redirect_stderr(errors), self.assertRaises(RuntimeError):
            projection('/fixture', 'vm-demo-source')
        self.assertEqual(errors.getvalue(), 'Nix fixture failure\n')

    def test_projection_refuses_untracked_absolute_file_reads(self):
        from native_controller import projection
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'recipe'
            root.mkdir()
            external = Path(directory) / 'outside-recipe'
            external.write_text('/nix/store/' + 'a' * 32 + '-nixos-system-external')
            (root / 'flake.nix').write_text('{ outputs={ self }: { nixberrySystems.test.config={ nixberry.backup.enable=true; system.build.toplevel.outPath=builtins.readFile ' + json.dumps(str(external)) + '; }; }; }')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(RuntimeError):
                projection(str(root), 'test')

    def test_projection_cannot_resolve_missing_lock_entries(self):
        from native_controller import projection
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'recipe'
            root.mkdir()
            dependency = Path(directory) / 'dependency'
            dependency.mkdir()
            (dependency / 'flake.nix').write_text('{ outputs={ self }: { system="/nix/store/' + 'a'*32 + '-nixos-system-unlocked"; }; }')
            (root / 'flake.nix').write_text('{ inputs.extra.url=' + json.dumps('path:'+str(dependency)) + '; outputs={ self,extra }: { nixberrySystems.test.config={ nixberry.backup.enable=true; system.build.toplevel.outPath=extra.system; }; }; }')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(RuntimeError):
                projection(str(root), 'test')


class NativeTransportTests(unittest.TestCase):
    def test_binary_transfer_forces_no_tty_and_preserves_archive_bytes(self):
        import hashlib
        import sys
        from native_controller import remote
        contents = b'\x00\x03\r\nsealed recipe bytes\xff'
        with tempfile.TemporaryDirectory() as directory:
            command = Path(directory) / 'ssh-fixture'
            command.write_text('#!' + sys.executable + '\nimport hashlib,json,sys\n'
                'print(json.dumps({"sha256":hashlib.sha256(sys.stdin.buffer.read()).hexdigest(), "args":sys.argv[1:]}))\n')
            command.chmod(0o700)
            result = remote([str(command), '--', 'root@fixture'], 'source-command', 5, input=contents)
        self.assertEqual(result['sha256'], hashlib.sha256(contents).hexdigest())
        self.assertIn('-T', result['args'])
        self.assertIn('ForwardAgent=no', result['args'])
