"""Backup policies, provider isolation, plan boundaries and scheduling contracts."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backups
from cli import main, operations
from check_inventory import nix
from check_ci import public_recipe


def settings(**extra):
    return {'enable': True, 'provider': 'configurations/server/provider.py',
            'repository': '/private/backups', 'identityFile': '/private/identity',
            'recipient': 'age1' + 'a' * 58, 'restoreBackends': ['libvirt'],
            'schedule': 'daily', 'keepLast': 3, **extra}


class BackupPolicyTests(unittest.TestCase):
    def test_rpi_controller_policy_does_not_change_installed_or_recovery_systems(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / 'recipe'
            public_recipe(source, recipe)
            target = recipe / 'management/rpi/targets/rpi-demo.nix'
            original = (recipe / 'management/examples/rpi.nix').read_text()
            expression = 'let f=builtins.getFlake ' + nix('path:' + str(recipe)) + '; in {' \
                'system=f.nixberrySystems.rpi-demo.config.system.build.toplevel.outPath;' \
                'recovery=f.nixberryRpiRecoveries.rpi-demo.config.system.build.toplevel.outPath;' \
                'hardwareFields=builtins.attrNames f.nixberrySystems.rpi-demo.config.nixberry.rpi; }'
            values = []
            for text in [original, original.rsplit('}', 1)[0] + 'backup=' + nix(settings()) + '; }']:
                target.write_text(text)
                result = subprocess.run(['nix','eval','--impure','--json','--no-write-lock-file','--expr',expression],
                                        capture_output=True,text=True,timeout=240,check=True)
                values.append(json.loads(result.stdout))
            self.assertEqual(values[0], values[1])
            self.assertNotIn('backup', values[1]['hardwareFields'])

    def test_nix_and_python_validation_agree(self):
        values = [{}, settings(), settings(keepLast=2), settings(schedule=None), settings(monitor={'enable':True,'maxAgeHours':24})]
        invalid = [settings(identityFile='/nix/store/secret'), settings(repository='./relative'),
                   settings(provider='../private.py'), settings(provider='configurations/../bad.py'),
                   settings(keepLast=True), settings(keepLast=1), settings(schedule='daily\nExecStart=bad'),
                   settings(schedule='daily\tbad'), settings(identityFile='/private/key\x7f'),
                   settings(restoreBackends=['typo']), settings(unknown=True), {'enable': 'yes'},
                   settings(monitor={'maxAgeHours':0}), settings(monitor={'notify':'yes'}),
                   settings(monitor={'intervalMinutes':True}), settings(monitor={'unknown':True})]
        path = Path(__file__).with_name('backup-policy.nix')
        for value in values + invalid:
            result = subprocess.run(['nix', 'eval', '--impure', '--json', '--expr',
                '(import ' + str(path) + ') ' + nix(value)], capture_output=True, text=True, timeout=10)
            if value in invalid:
                self.assertNotEqual(result.returncode, 0, value)
                with self.assertRaises(ValueError): backups.policy({'backup': value})
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), backups.policy({'backup': value}))

    def test_all_backend_method_access_variants_have_matching_operations(self):
        targets = []
        for backend in backups.BACKENDS:
            for method in ['image', 'iso']:
                for offline, direct in [(False, False), (True, False), (True, True)]:
                    if backend == 'rpi' and offline:
                        continue
                    for enabled in [False, True]:
                        target = {'backend': backend, 'deploymentMethod': method, 'offline': offline,
                                  'commands': {key: key for key in ['probe', 'create', 'install', 'provision', 'reset']}
                                    if not offline and backend != 'rpi' else {},
                                  'backup': settings(enable=enabled),
                                  'capabilities': {'system': not offline or direct, 'hypervisor': not offline and backend != 'rpi'}}
                        if direct:
                            target['access'] = {'host': '10.0.1.19', 'user': 'root', 'port': 22}
                        targets.append(target)
        expression = 'map (import ' + str(Path(__file__).with_name('operations.nix')) + ') ' + nix(targets)
        result = subprocess.run(['nix', 'eval', '--impure', '--json', '--expr', expression],
                                capture_output=True, text=True, check=True)
        for target, values in zip(targets, json.loads(result.stdout), strict=True):
            self.assertEqual(set(values), set(operations(target)))
            self.assertEqual('backup' in values, target['backup']['enable'] and target['capabilities']['system'])
            self.assertEqual('backups' in values, target['backup']['enable'])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.recipe = self.root / 'recipe'
        self.environment = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'controller-state')})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.recipe.mkdir(); (self.recipe / 'flake.nix').write_text('{}')
        (self.recipe / 'nixberry').write_text('#!/bin/sh\nexit 0\n')
        self.provider = self.recipe / 'configurations/server/provider.py'
        self.provider.parent.mkdir(parents=True)
        self.provider.write_text("import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'protocol':1,'ok':True,'result':{'request':r}}))\n")
        self.target = {'backend': 'libvirt', 'spec': {}, 'host': 'lab',
                       'capabilities': {'system': True, 'hypervisor': True}, 'backup': settings()}
        self.registry = {'source': self.target, 'destination': {**self.target, 'backup': {}}}

    def run_provider(self, action='backup', **kwargs):
        return backups.run(str(self.recipe), self.registry, action, 'source', **kwargs)

    def test_explicit_recipe_apply_and_snapshot_are_preserved(self):
        with patch.dict(os.environ, {'NIXBERRY_RECIPE': '/wrong/interactive/recipe'}):
            result = self.run_provider(apply=True)
        self.assertEqual(result['request']['recipe'], str(self.recipe))
        self.assertTrue(result['request']['apply'])
        result = backups.run(str(self.recipe), self.registry, 'restore', 'destination',
                             source='source', snapshot='b' * 32)
        self.assertFalse(result['request']['apply'])
        self.assertEqual(result['request']['snapshotId'], 'b' * 32)

    def test_dry_run_does_not_execute_provider_or_inspect_guest(self):
        self.provider.write_text('raise AssertionError("must not run")')
        result = self.run_provider(dry_run=True)
        self.assertFalse(result['applied']); self.assertEqual(result['readiness'], 'not-checked')

    def test_key_and_storage_paths_inside_recipe_or_linked_private_configuration_are_rejected(self):
        private = self.root / 'private-configuration'; private.mkdir()
        (self.recipe / 'configurations/private').symlink_to(private, target_is_directory=True)
        for field in ['identityFile', 'repository']:
            for directory in [self.recipe, private]:
                self.registry['source']['backup'] = settings(**{field: str(directory / 'unsafe')})
                with self.assertRaisesRegex(ValueError, 'outside the recipe'):
                    self.run_provider(dry_run=True)

    def test_missing_snapshot_same_target_and_unsupported_backend_fail_before_provider(self):
        self.provider.write_text('raise AssertionError("must not run")')
        cases = [('verify-backup', 'source', {}),
                 ('restore', 'source', {'source': 'source', 'snapshot': 'b' * 32}),
                 ('restore', 'destination', {'source': 'missing', 'snapshot': 'b' * 32})]
        for action, target, options in cases:
            with self.assertRaises(ValueError):
                backups.run(str(self.recipe), self.registry, action, target, **options)
        self.registry['destination']['backend'] = 'rpi'
        with self.assertRaises(ValueError):
            backups.run(str(self.recipe), self.registry, 'restore', 'destination', source='source', snapshot='b'*32)

    def test_provider_cannot_escape_captured_configuration_or_inject_error_text(self):
        outside = self.root / 'outside.py'; outside.write_text('raise AssertionError("must not run")')
        self.provider.unlink(); self.provider.symlink_to(outside)
        with self.assertRaises(ValueError): self.run_provider()
        self.provider.unlink()
        self.provider.write_text("import json\nprint(json.dumps({'protocol':1,'ok':False,'error':'secret\\ntext'}))")
        with self.assertRaisesRegex(RuntimeError, 'provider_failed') as result: self.run_provider()
        self.assertNotIn('secret', str(result.exception))

    def test_schedule_bundle_is_private_reviewable_and_never_installed(self):
        output = self.root / 'units'
        with patch('subprocess.run') as run:
            result = backups.schedule(self.recipe, 'source', settings(), output, True)
        run.assert_not_called(); self.assertFalse(output.exists()); self.assertFalse(result['enabled'])
        result = backups.schedule(self.recipe, 'source', settings(), output, False)
        self.assertTrue(result['filesWritten']); self.assertFalse(result['installed'])
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        service = (output / 'nixberry-backup-source.service').read_text()
        self.assertIn('NIXBERRY_RECIPE=' + str(self.recipe), service)
        self.assertIn('backup-prune', service)
        self.assertNotIn('enable --now', service)
        with self.assertRaises(ValueError): backups.schedule(self.recipe, 'source', settings(), output, False)
        for target in ['../outside', 'source\nExecStart=bad', 'source%H']:
            with self.assertRaises(ValueError):
                backups.schedule(self.recipe, target, settings(), self.root / 'never-created', False)
        self.assertFalse((self.root / 'never-created').exists())

    def test_cli_json_and_invalid_option_boundaries(self):
        manifest = self.root / 'manifest.json'; manifest.write_text(json.dumps(self.registry))
        environment = {'NIXBERRY_RECIPE': str(self.recipe), 'NIXBERRY_DEPLOYMENT': str(manifest)}
        with patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['backup', 'source', '--dry-run', '--json']), 0)
        self.assertFalse(json.loads(output.getvalue())['applied'])
        for arguments in [['backup', 'source', '--snapshot', 'b'*32], ['backups', 'source', '--apply'],
                          ['restore', 'destination', '--output', '/tmp/no'], ['verify-backup', 'source', '--from', 'source']]:
            with patch.dict(os.environ, environment), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(arguments)
