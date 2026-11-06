"""Activation cannot overlap captures from either generation's policy."""
import contextlib
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import snapshots
from test_snapshots import settings
import update_guard

OLD = '/nix/store/' + 'a' * 32 + '-nixos-system-old-test'
NEW = '/nix/store/' + 'b' * 32 + '-nixos-system-new-test'


class UpdateGuardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.old = settings(self.root / 'old')
        self.new = settings(self.root / 'new')
        self.root.joinpath('old').mkdir()
        self.root.joinpath('new').mkdir()
        for name, value in [('snapshots.OWNER_UID', os.getuid()),
                            ('update_guard.running_system', OLD),
                            ('update_guard.policies', [self.old, self.new])]:
            item = patch(name, return_value=value) if name != 'snapshots.OWNER_UID' else patch(name, value)
            item.start()
            self.addCleanup(item.stop)

    def test_plan_creates_no_state_and_never_activates(self):
        with patch('update_guard.subprocess.run') as run:
            report = update_guard.activate(OLD, NEW)
        self.assertFalse(report['applied'])
        run.assert_not_called()
        self.assertFalse(Path(self.old['stateDirectory']).exists())
        self.assertFalse(Path(self.new['stateDirectory']).exists())

    def test_both_generation_locks_cover_profile_change_and_activation(self):
        commands = []
        def execute(command, **kwargs):
            for policy in (self.old, self.new):
                with self.assertRaisesRegex(RuntimeError, 'holds the capture lock'):
                    with snapshots.lock(policy):
                        self.fail('Capture entered during activation')
            commands.append(command)
        with patch('update_guard.subprocess.run', side_effect=execute):
            self.assertTrue(update_guard.activate(OLD, NEW, apply=True)['applied'])
        self.assertEqual(commands, [['nix-env', '-p', '/nix/var/nix/profiles/system', '--set', NEW],
                                    [NEW + '/bin/switch-to-configuration', 'switch']])
        with snapshots.lock(self.old), snapshots.lock(self.new):
            pass

    def test_active_capture_on_either_policy_prevents_profile_changes(self):
        for busy in (self.old, self.new):
            with self.subTest(state=busy['stateDirectory']), snapshots.lock(busy), \
                    patch('update_guard.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'holds the capture lock'):
                    update_guard.activate(OLD, NEW, apply=True)
                run.assert_not_called()

    def test_same_state_directory_is_locked_once_and_disabled_policies_are_supported(self):
        for policies in ([self.old, copy.deepcopy(self.old)], [self.old], [], [self.new]):
            with self.subTest(policies=len(policies)), \
                    patch('update_guard.policies', return_value=policies), \
                    patch('update_guard.subprocess.run') as run:
                update_guard.activate(OLD, NEW, apply=True)
                self.assertEqual(run.call_count, 2)

    def test_pending_recovery_never_changes_profile(self):
        with patch('snapshots.pending', return_value={'activeUnits': ['web.service']}), \
                patch('update_guard.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'writer recovery'):
                update_guard.activate(OLD, NEW, apply=True)
            run.assert_not_called()

    def test_generation_or_policy_change_under_lock_never_activates(self):
        for overrides in (
                {'running_system': [OLD, NEW]},
                {'policies': [[self.old, self.new], [self.new]]}):
            with self.subTest(overrides=list(overrides)), contextlib.ExitStack() as stack:
                for key, value in overrides.items():
                    stack.enter_context(patch('update_guard.' + key, side_effect=value))
                run = stack.enter_context(patch('update_guard.subprocess.run'))
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    update_guard.activate(OLD, NEW, apply=True)
                run.assert_not_called()

    def test_malformed_system_or_foreign_running_generation_never_activates(self):
        for previous, system in ((OLD, NEW + ';reboot'), ('/tmp/system', NEW), (NEW, NEW)):
            with self.subTest(previous=previous, system=system), patch('update_guard.subprocess.run') as run:
                with self.assertRaises((ValueError, RuntimeError)):
                    update_guard.activate(previous, system, apply=True)
                run.assert_not_called()
