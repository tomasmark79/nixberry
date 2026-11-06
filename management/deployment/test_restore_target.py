"""Prepared targets keep writers held and expose readiness without mutations."""
import json
import contextlib
import io
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import restore_target
import snapshots

SYSTEM = '/nix/store/' + 'a' * 32 + '-nixos-system-restore-test'
MACHINE = 'b' * 32


class RestoreTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.settings = {'version': 1, 'architecture': 'x86_64-linux',
            'stateDirectory': str(self.root / 'state'), 'automaticUnits': [],
            'applications': {'web': {'paths': [str(self.data)], 'units': ['writer.service', 'app.socket']}}}
        self.guard = self.root / 'zz-nixberry-restore.conf'
        self.guard.write_text(restore_target.guard_text(self.settings))
        self.active = {'writer.service': 'inactive', 'app.socket': 'inactive'}
        self.reload = 'no'
        self.conditions = None
        self.commands = []
        self.real_run = snapshots.run
        def run(command, **kwargs):
            self.commands.append(command)
            if command[0] == 'busctl':
                return json.dumps({'type': 'a(sbbsi)', 'data': self.conditions if self.conditions is not None else
                    [['ConditionPathExists', False, False, str(restore_target.marker_path(self.settings)), 0]]})
            self.assertEqual(command[:2], ['systemctl', 'show'])
            return ('LoadState=loaded\nActiveState=' + self.active[command[-1]]
                    + '\nNeedDaemonReload=' + self.reload + '\nDropInPaths=' + str(self.guard) + '\n')
        for item in (patch('snapshots.OWNER_UID', os.getuid()), patch('snapshots.run', side_effect=run),
                     patch('snapshots.machine_id', return_value=MACHINE),
                     patch('snapshots.running_system', return_value=SYSTEM),
                     patch('restore_target.platform.machine', return_value='x86_64')):
            item.start()
            self.addCleanup(item.stop)

    def test_empty_target_reports_ready_without_creating_state_or_changing_units(self):
        before = sorted(self.root.rglob('*'))
        report = restore_target.status(self.settings, expected_system=SYSTEM)
        self.assertTrue(report['ready'])
        self.assertFalse(report['completed'])
        self.assertEqual(report['machineId'], MACHINE)
        self.assertEqual(sorted(self.root.rglob('*')), before)
        self.assertFalse((self.root / 'state').exists())

    def test_query_failures_keep_the_target_context_and_forward_external_diagnostics(self):
        for failure in (subprocess.CompletedProcess([], 1, '', 'driver failure\n'),
                        subprocess.TimeoutExpired('systemctl', 15, stderr='driver timeout\n')):
            arguments = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with self.subTest(failure=type(failure).__name__), patch('snapshots.run', self.real_run), \
                    patch('snapshots.subprocess.run', **arguments), contextlib.redirect_stderr(io.StringIO()) as output:
                with self.assertRaisesRegex((RuntimeError, TimeoutError), 'Destination writer inspection'):
                    restore_target.unit_state(self.settings, 'writer.service')
                self.assertEqual(output.getvalue(), failure.stderr)

    def test_populated_paths_and_running_writers_are_not_ready(self):
        (self.data / 'existing').write_text('keep')
        self.assertFalse(restore_target.status(self.settings)['ready'])
        (self.data / 'existing').unlink()
        self.active['writer.service'] = 'active'
        self.assertFalse(restore_target.status(self.settings)['ready'])
        self.assertEqual(self.active['writer.service'], 'active')

    def test_missing_paths_are_empty_but_symlinks_are_refused(self):
        self.settings['applications']['web']['paths'] = [str(self.root / 'missing/leaf')]
        self.guard.write_text(restore_target.guard_text(self.settings))
        self.assertTrue(restore_target.status(self.settings)['ready'])
        (self.root / 'missing').symlink_to(self.data)
        with self.assertRaises(ValueError):
            restore_target.status(self.settings)

    def test_absent_stale_or_edited_guard_and_reload_are_refused(self):
        for problem in ('absent', 'edited', 'reload'):
            with self.subTest(problem=problem):
                self.guard.write_text(restore_target.guard_text(self.settings))
                self.reload = 'no'
                if problem == 'absent': self.guard.unlink()
                elif problem == 'edited': self.guard.write_text('[Unit]\nConditionPathExists=/wrong\n')
                else: self.reload = 'yes'
                with self.assertRaises(RuntimeError):
                    restore_target.status(self.settings)

    def test_loaded_guard_does_not_count_if_effective_conditions_reset_or_weaken_it(self):
        marker = str(restore_target.marker_path(self.settings))
        for conditions in ([], [['ConditionPathExists', True, False, marker, 0]],
                           [['ConditionPathExists', False, True, marker, 0]]):
            self.conditions = conditions
            with self.subTest(conditions=conditions), self.assertRaises(RuntimeError):
                restore_target.status(self.settings)

    def test_expected_generation_and_architecture_are_checked_before_units(self):
        with self.assertRaises(RuntimeError):
            restore_target.status(self.settings, expected_system=SYSTEM.replace('a'*32, 'c'*32))
        self.settings['architecture'] = 'aarch64-linux'
        with self.assertRaises(RuntimeError):
            restore_target.status(self.settings)
        self.assertEqual(self.commands, [])

    def test_completion_namespace_binds_policy_and_machine_and_validates_record(self):
        old = restore_target.marker_path(self.settings)
        other = json.loads(json.dumps(self.settings))
        other['applications']['web']['units'].append('app.timer')
        self.assertNotEqual(old, restore_target.marker_path(other))
        with patch('snapshots.machine_id', return_value='c'*32):
            self.assertNotEqual(old, restore_target.marker_path(self.settings))
        old.parent.mkdir(mode=0o700)
        record = {'version': 1, 'machineId': MACHINE, 'policySha256': restore_target.policy_hash(self.settings),
                  'sourceId': 'vm-demo-source', 'snapshotId': 'd'*64, 'systemPath': SYSTEM, 'completedAt': 123}
        snapshots.write_record(old, record)
        self.assertTrue(restore_target.status(self.settings)['completed'])
        self.assertFalse(restore_target.status(self.settings)['ready'])
        snapshots.write_record(old, {**record, 'machineId': 'c'*32})
        with self.assertRaises(ValueError):
            restore_target.status(self.settings)

    def test_state_overlap_and_management_unit_holds_are_refused(self):
        for unit in ('sshd.service', 'ssh.socket', 'nixberry-update.service', 'wg-quick@wg0.service', '../escape.service'):
            self.settings['applications']['web']['units'] = [unit]
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                restore_target.policy(self.settings)
        self.settings['applications']['web']['units'] = []
        self.settings['applications']['web']['paths'] = [str(self.root)]
        with self.assertRaises(ValueError):
            restore_target.policy(self.settings)

    def test_destination_dns_hold_does_not_relax_source_capture_network_protection(self):
        for unit in ('dnsmasq.service', 'adguardhome.service'):
            self.settings['applications']['web']['units'] = [unit]
            self.assertEqual(restore_target.policy(self.settings), self.settings)
            with self.assertRaises(ValueError):
                snapshots.unit_name(unit)


class RestoreProcessTests(unittest.TestCase):
    def test_applied_restore_requires_the_managed_service_main_process_and_group_cleanup(self):
        good = 'MainPID=' + str(os.getpid()) + '\nKillMode=control-group\nActiveState=active\n'
        with patch('snapshots.run', return_value=good):
            restore_target.require_managed()
        for bad in (good.replace('control-group', 'process'), good.replace(str(os.getpid()), '0')):
            with self.subTest(bad=bad), patch('snapshots.run', return_value=bad), self.assertRaises(RuntimeError):
                restore_target.require_managed()
