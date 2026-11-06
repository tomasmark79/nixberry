"""Maintenance entry points preserve read-only health and explicit prune authority."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cli
import native_restic


class MaintenanceCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nixberry-maintenance-cli-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.name = 'vm-demo-source'
        self.system = '/nix/store/' + 'a' * 32 + '-nixos-system-vm-demo-source'
        self.generation = self.system
        self.machine = 'b' * 32
        self.recipe = self.root / 'recipe'
        self.recipe.mkdir()
        self.data = self.root / 'application'
        self.data.mkdir()
        self.capture = {'version': 1, 'applications': {'web': {'paths': [str(self.data)], 'stopUnits': []}},
            'roots': {'system': {'path': '/', 'snapshotDirectory': str(self.root / 'snapshots')}},
            'stateDirectory': str(self.root / 'capture-state'), 'minFreeBytes': 1024, 'timeoutSeconds': 60}
        self.settings = {'version': 1, 'sourceId': 'libvirt:vm-demo-source', 'captureMethod': 'cold',
            'capture': self.capture, 'repository': str(self.root / 'repository'),
            'passwordFile': str(self.root / 'password'), 'repositoryIdFile': str(self.root / 'repository-id'),
            'recoveryFile': str(self.root / 'recovery.tar'), 'runtimeDirectory': str(self.root / 'runtime'),
            'timeoutSeconds': 60, 'ssh': None}
        self.maintenance = {'version': 1, 'keepLast': 2, 'maxAgeSeconds': 3600}
        self.config = self.root / 'backup.json'
        self.config.write_text(json.dumps(self.settings))
        self.maintenance_file = self.root / 'maintenance.json'
        self.maintenance_file.write_text(json.dumps(self.maintenance))
        for key, contents in (('passwordFile', 'test-only-password'), ('repositoryIdFile', 'c' * 64)):
            path = Path(self.settings[key])
            path.write_text(contents)
            path.chmod(0o600)
        self.access = self.root / 'access.json'
        self.access.write_text(json.dumps({'version': 1, 'target': self.name,
            'sourceId': self.settings['sourceId'], 'repository': self.settings['repository'],
            'repositoryId': 'c' * 64, 'passwordFile': self.settings['passwordFile'],
            'ssh': None, 'runtimeDirectory': self.settings['runtimeDirectory'], 'timeoutSeconds': 60}))
        self.access.chmod(0o600)
        self.registry = self.root / 'registry.json'
        self.save_target()
        self.commands = []
        def execute(reader, arguments, **kwargs):
            self.assertTrue(reader.read_only, 'Maintenance CLI opened a writable repository')
            self.commands.append(arguments)
            if arguments == ['cat', 'config']:
                return json.dumps({'id': 'c' * 64})
            if arguments == ['snapshots', '--tag', 'nixberry-receipt-v1,source:libvirt:vm-demo-source']:
                return '[]'
            self.fail('Unexpected storage operation: ' + repr(arguments))
        for item in (
                patch.dict(os.environ, {'NIXBERRY_LANG': 'en', 'NIXBERRY_RECIPE': str(self.recipe),
                                       'NIXBERRY_DEPLOYMENT': str(self.registry)}),
                patch('snapshots.OWNER_UID', os.getuid()),
                patch('snapshots.machine_id', return_value=self.machine),
                patch('native_restic.running_system', side_effect=lambda: self.generation),
                patch('native_restic.signal.signal'), patch('native_restic.require_tmpfs'),
                patch('native_restic.Restic._execute', execute),
                patch('subprocess.run', side_effect=AssertionError('CLI test invoked an external command'))):
            item.start()
            self.addCleanup(item.stop)

    def save_target(self, backend='libvirt', *, legacy=False, offline=False):
        target = {'backend': backend, 'host': 'vm-demo-host', 'configuration': 'default',
                  'commands': {}, 'spec': {'name': self.name}, 'offline': offline}
        if legacy:
            target['backup'] = {'enable': True, 'provider': 'configurations/server/provider.py',
                'repository': '/private/backups', 'identityFile': '/private/identity',
                'recipient': 'age1' + 'a' * 58, 'restoreBackends': ['libvirt']}
        self.registry.write_text(json.dumps({self.name: target}))

    def invoke(self, entry, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = entry(arguments)
        self.assertEqual(errors.getvalue(), '')
        return code, json.loads(output.getvalue())

    def guest(self, action, *arguments):
        with patch('native_restic.os.geteuid', return_value=0):
            return self.invoke(native_restic.main, [action, '--config', str(self.config),
                '--maintenance', str(self.maintenance_file), '--json', *arguments])

    def test_guest_health_without_apply_reads_pinned_storage_and_returns_nonzero_when_missing(self):
        code, report = self.guest('backup-check')
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'missing')
        self.assertEqual(report['maxAgeSeconds'], 3600)
        self.assertEqual(self.commands, [['cat', 'config'],
            ['snapshots', '--tag', 'nixberry-receipt-v1,source:libvirt:vm-demo-source']])
        self.assertFalse(Path(self.capture['stateDirectory']).exists())

    def test_guest_prune_previews_without_apply_and_takes_a_lease_only_with_apply(self):
        code, report = self.guest('backup-prune')
        self.assertEqual(code, 0)
        self.assertFalse(report['applied'])
        self.assertEqual(report['remove'], [])
        self.assertFalse(Path(self.capture['stateDirectory']).exists())
        code, report = self.guest('backup-prune', '--apply')
        self.assertEqual(code, 0)
        self.assertTrue(report['applied'])
        self.assertEqual(report['remove'], [])
        self.assertTrue((Path(self.capture['stateDirectory']) / 'lock').is_file())

    def test_guest_maintenance_dry_run_never_opens_storage_or_creates_runtime(self):
        for action in ('backup-check', 'backup-prune'):
            with self.subTest(action=action):
                code, report = self.guest(action, '--dry-run')
                self.assertEqual(code, 0)
                self.assertEqual(report, {'action': action, 'maintenance': self.maintenance,
                    'applied': False, 'readiness': 'not-checked'})
        self.assertEqual(self.commands, [])
        self.assertFalse(Path(self.settings['runtimeDirectory']).exists())
        self.assertFalse(Path(self.capture['stateDirectory']).exists())

    def test_guest_health_apply_is_refused_before_reading_configuration(self):
        with patch('pathlib.Path.read_text', side_effect=AssertionError('Invalid health mode read configuration')), \
                self.assertRaises(SystemExit) as failure:
            self.guest('backup-check', '--apply')
        self.assertEqual(failure.exception.code, 2)

    def test_guest_prune_captures_generation_before_reading_configuration(self):
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == self.config:
                self.generation = '/nix/store/' + 'd' * 32 + '-nixos-system-vm-demo-changed'
            return original(path, *args, **kwargs)
        with patch('pathlib.Path.read_text', read), \
                patch('native_maintenance.reader', side_effect=AssertionError('Changed generation opened storage')), \
                self.assertRaises(RuntimeError):
            self.guest('backup-prune', '--apply')
        self.assertEqual(self.commands, [])
        self.assertFalse(Path(self.capture['stateDirectory']).exists())

    def test_global_independent_health_needs_no_registry_or_source_and_returns_nonzero(self):
        with patch.dict(os.environ, {'NIXBERRY_DEPLOYMENT': str(self.root / 'absent-registry')}), \
                patch('snapshots.machine_id', side_effect=AssertionError('Independent health inspected the source')):
            code, report = self.invoke(cli.main, ['backup-check', self.name, '--backup-access', str(self.access),
                                                '--max-age-seconds', '3600', '--json'])
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'missing')
        self.assertEqual(report['sourceId'], 'libvirt:vm-demo-source')
        self.assertEqual(len(self.commands), 2)
        self.assertFalse(Path(self.capture['stateDirectory']).exists())

    def test_global_independent_dry_run_never_opens_storage_or_creates_runtime(self):
        code, report = self.invoke(cli.main, ['backup-check', self.name, '--backup-access', str(self.access),
                                            '--max-age-seconds', '3600', '--dry-run', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(report['readiness'], 'not-checked')
        self.assertEqual(report['maxAgeSeconds'], 3600)
        self.assertFalse(report['applied'])
        self.assertEqual(self.commands, [])
        self.assertFalse(Path(self.settings['runtimeDirectory']).exists())

    def test_global_independent_health_apply_is_refused_before_access(self):
        with patch('native_access.load', side_effect=AssertionError('Invalid health mode opened access')), \
                self.assertRaises(ValueError):
            self.invoke(cli.main, ['backup-check', self.name, '--backup-access', str(self.access),
                                   '--max-age-seconds', '3600', '--apply', '--json'])

    def test_invalid_independent_age_is_refused_before_access_or_registry_reads(self):
        with patch('native_access.load', side_effect=AssertionError('Invalid age opened access')), \
                patch('pathlib.Path.read_text', side_effect=AssertionError('Invalid age read a registry')):
            for value in (None, '0', '-1', '31536001', 'not-a-number'):
                arguments = ['backup-check', self.name, '--backup-access', str(self.access), '--json']
                if value is not None:
                    arguments += ['--max-age-seconds', value]
                with self.subTest(value=value):
                    if value == 'not-a-number':
                        with self.assertRaises(SystemExit) as failure:
                            self.invoke(cli.main, arguments)
                        self.assertEqual(failure.exception.code, 2)
                    else:
                        with self.assertRaises(ValueError):
                            self.invoke(cli.main, arguments)

    def test_age_override_is_rejected_for_source_and_other_storage_operations_before_registry(self):
        for arguments in (['backup-check', self.name],
                          ['backups', self.name, '--backup-access', str(self.access)]):
            with self.subTest(arguments=arguments), \
                    patch('pathlib.Path.read_text', side_effect=AssertionError('Invalid age option read registry')), \
                    self.assertRaises(SystemExit) as failure:
                self.invoke(cli.main, [*arguments, '--max-age-seconds', '3600', '--json'])
            self.assertEqual(failure.exception.code, 2)

    def test_global_source_maintenance_routes_without_apply_and_preserves_health_exit_codes(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware', 'rpi'):
            self.save_target(backend)
            for status, expected in (('ok', 0), ('missing', 1), ('stale', 1), ('future', 1)):
                calls = []
                def source(recipe, target, action, name, **kwargs):
                    calls.append((target['backend'], action, name, kwargs['apply'], kwargs['dry_run']))
                    return {'status': status}
                with self.subTest(backend=backend, status=status), patch('cli.run_native', source), \
                        patch('cli.run_backup', side_effect=AssertionError('Native health used a legacy provider')):
                    code, report = self.invoke(cli.main, ['backup-check', self.name, '--json'])
                self.assertEqual(code, expected)
                self.assertEqual(report['status'], status)
                self.assertEqual(calls, [(backend, 'backup-check', self.name, False, False)])

    def test_global_prune_routes_preview_and_apply_to_the_native_source(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware', 'rpi'):
            self.save_target(backend)
            for apply in (False, True):
                calls = []
                def source(recipe, target, action, name, **kwargs):
                    calls.append((target['backend'], action, name, kwargs['apply'], kwargs['dry_run']))
                    return {'applied': apply, 'remove': ['d' * 64]}
                with self.subTest(backend=backend, apply=apply), patch('cli.run_native', source), \
                        patch('cli.run_backup', side_effect=AssertionError('Native prune used a legacy provider')):
                    code, report = self.invoke(cli.main, ['backup-prune', self.name, '--json',
                                                         *(['--apply'] if apply else [])])
                self.assertEqual(code, 0)
                self.assertEqual(report['applied'], apply)
                self.assertEqual(calls, [(backend, 'backup-prune', self.name, apply, False)])

    def test_global_source_dry_run_uses_native_plan_without_contacting_storage_or_source(self):
        projection = {'enabled': True, 'captureMethod': 'cold', 'capture': self.capture,
                      'restic': self.settings, 'maintenance': self.maintenance, 'systemPath': self.system}
        with patch('native_controller.freeze_recipe', return_value=str(self.recipe)), \
                patch('native_controller.projection', return_value=projection), \
                patch('native_controller.execute', side_effect=AssertionError('Dry-run contacted a source')):
            for action in ('backup-check', 'backup-prune'):
                with self.subTest(action=action):
                    code, report = self.invoke(cli.main, [action, self.name, '--dry-run', '--json'])
                    self.assertEqual(code, 0)
                    self.assertEqual(report['readiness'], 'not-checked')
                    self.assertEqual(report['maintenance'], self.maintenance)
                    self.assertFalse(report['applied'])
        self.assertEqual(self.commands, [])
        self.assertFalse(Path(self.settings['runtimeDirectory']).exists())

    def test_global_source_health_apply_is_refused_before_source_preparation(self):
        with patch('native_controller.freeze_recipe', side_effect=AssertionError('Invalid health mode prepared source')), \
                self.assertRaises(ValueError):
            self.invoke(cli.main, ['backup-check', self.name, '--apply', '--json'])

    def test_global_independent_health_forwards_age_and_preserves_all_exit_codes(self):
        for status, expected in (('ok', 0), ('missing', 1), ('stale', 1), ('future', 1)):
            calls = []
            def access(path, name, action, **kwargs):
                calls.append((path, name, action, kwargs['apply'], kwargs['max_age_seconds']))
                return {'status': status}
            with self.subTest(status=status), patch('cli.run_native_access', access):
                code, report = self.invoke(cli.main, ['backup-check', self.name, '--backup-access', str(self.access),
                                                    '--max-age-seconds', '900', '--json'])
            self.assertEqual(code, expected)
            self.assertEqual(report['status'], status)
            self.assertEqual(calls, [(str(self.access), self.name, 'backup-check', False, 900)])

    def test_legacy_health_keeps_apply_route_and_legacy_alarm_exit_semantics(self):
        self.save_target(legacy=True)
        for apply in (False, True):
            for report, expected in (({'alarm': False}, 0), ({'alarm': True}, 1),
                                     ({'alarm': False, 'notification': {'state': 'failed'}}, 1)):
                calls = []
                def provider(recipe, registry, action, name, **kwargs):
                    calls.append((action, name, kwargs['apply']))
                    return report
                with self.subTest(apply=apply, report=report), patch('cli.run_backup', provider), \
                        patch('cli.run_native', side_effect=AssertionError('Legacy health used native capture')):
                    code, output = self.invoke(cli.main, ['backup-check', self.name, '--json',
                                                         *(['--apply'] if apply else [])])
                self.assertEqual(code, expected)
                self.assertEqual(output, report)
                self.assertEqual(calls, [('backup-check', self.name, apply)])

    def test_new_maintenance_commands_do_not_expand_legacy_ssh_override_authority(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
            self.save_target(backend, legacy=True, offline=True)
            for action in ('backup-check', 'backup-prune'):
                for option in ('--ssh', '--host'):
                    with self.subTest(backend=backend, action=action, option=option), \
                            patch('cli.guest_connection', side_effect=AssertionError('Legacy override accepted endpoint')), \
                            patch('cli.run_backup', side_effect=AssertionError('Legacy override ran provider')), \
                            patch('cli.run_native', side_effect=AssertionError('Legacy override contacted source')), \
                            self.assertRaises(SystemExit) as failure:
                        self.invoke(cli.main, [action, self.name, option, 'root@192.0.2.123:22', '--json'])
                    self.assertEqual(failure.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
