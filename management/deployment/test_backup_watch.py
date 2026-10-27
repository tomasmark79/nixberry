"""Freshness, interrupted runs, private reports and notification state transitions."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import backup_watch as watch
import backups
from cli import main


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.recipe = self.root / 'recipe'; self.recipe.mkdir()
        (self.recipe / 'flake.nix').write_text('{}')
        (self.recipe / 'nixberry').write_text('#!/bin/sh\n')
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME':str(self.root/'state')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.settings = {'repository':str(self.root/'repository'), 'monitor':watch.monitor_policy({})}
        self.directory = watch.state_directory(self.recipe,'source',self.settings['repository'])
        self.now = int(time.time())

    def inventory(self, age=0, state='recorded-verified'):
        return {'snapshots':[{'snapshotId':'a'*32,'createdAt':self.now-age,'state':state}], 'pendingCaptures':0}

    def evaluate(self, inventory, **kwargs):
        return watch.evaluate('source',self.settings['monitor'],inventory,now=self.now,**kwargs)

    def test_age_boundary_missing_corrupt_partial_and_future_records(self):
        self.assertFalse(self.evaluate(self.inventory(36*3600))['alarm'])
        self.assertIn('backup_stale', self.evaluate(self.inventory(36*3600+1))['issues'])
        self.assertIn('no_verified_backup', self.evaluate({'snapshots':[]})['issues'])
        self.assertIn('invalid_snapshots', self.evaluate(self.inventory(state='invalid'))['issues'])
        value=self.inventory();value['pendingCaptures']=1
        self.assertIn('partial_captures',self.evaluate(value)['issues'])
        self.assertIn('clock_skew',self.evaluate(self.inventory(-600))['issues'])
        self.assertIn('invalid_inventory',self.evaluate({'snapshots':'wrong'})['issues'])

    def test_active_run_is_bounded_and_does_not_hide_cached_stale_backup(self):
        with watch.capture_run(self.directory,'source'):
            attempt=watch.load(self.directory,'last-run.json')
            report=self.evaluate(None,attempt=attempt,error='repository_busy')
            self.assertEqual(report['status'],'running');self.assertFalse(report['alarm'])
            self.assertIsNone(report['latest'])
            previous={'latest':{'snapshotId':'a'*32,'createdAt':self.now-37*3600}}
            self.assertIn('backup_stale',self.evaluate(None,attempt=attempt,previous=previous,error='repository_busy')['issues'])
            attempt['startedAt']=self.now-91*60
            self.assertIn('capture_overdue',self.evaluate(None,attempt=attempt,error='repository_busy')['issues'])

    def test_abrupt_exit_or_reused_pid_is_interruption(self):
        self.directory.mkdir(parents=True,mode=0o700)
        program='import os,sys;from pathlib import Path;from backup_watch import capture_run\nwith capture_run(Path(sys.argv[1]),"source"):os._exit(0)'
        subprocess.run([os.sys.executable,'-c',program,str(self.directory)],check=True,
                       env={**os.environ,'PYTHONPATH':str(Path(watch.__file__).parent)})
        attempt=watch.load(self.directory,'last-run.json')
        self.assertIn('capture_interrupted',self.evaluate(self.inventory(),attempt=attempt)['issues'])
        attempt.update(watch.process_identity(os.getpid()));attempt['startTicks']+=1
        self.assertIn('capture_interrupted',self.evaluate(self.inventory(),attempt=attempt)['issues'])

    def test_failed_and_successful_attempts_preserve_history_and_measure_duration(self):
        with watch.capture_run(self.directory,'source'): pass
        previous=watch.load(self.directory,'last-run.json')
        error=RuntimeError('not persisted secret');error.backup_code='controlled_failure'
        with self.assertRaises(RuntimeError),watch.capture_run(self.directory,'source'):
            raise error
        failed=watch.load(self.directory,'last-run.json')
        self.assertEqual(failed['lastSucceededAt'],previous['lastSucceededAt'])
        self.assertEqual(failed['errorCode'],'controlled_failure')
        self.assertNotIn('secret',json.dumps(failed))
        self.assertIn('last_capture_failed',self.evaluate(self.inventory(),attempt=failed)['issues'])
        with watch.capture_run(self.directory,'source'): pass
        recovered=watch.load(self.directory,'last-run.json')
        self.assertEqual(recovered['state'],'succeeded');self.assertGreaterEqual(recovered['durationSeconds'],0)

    def test_read_only_check_creates_no_files_and_needs_no_identity(self):
        with patch('subprocess.run') as run:
            report=watch.check(self.recipe,'source',self.settings,self.inventory)
        run.assert_not_called();self.assertFalse(self.directory.exists())
        self.assertFalse(report['recorded']);self.assertFalse(report['freshCryptographicVerification'])

    def test_alert_dedup_repeat_recovery_and_delivery_failure(self):
        response=subprocess.CompletedProcess([],0,stdout='',stderr='')
        stale=self.inventory(age=37*3600)
        with patch('subprocess.run',return_value=response) as send:
            first=watch.check(self.recipe,'source',self.settings,lambda:stale,True)
            second=watch.check(self.recipe,'source',self.settings,lambda:stale,True)
            self.assertEqual(first['notification']['state'],'sent');self.assertEqual(second['notification']['state'],'suppressed')
            self.assertEqual(send.call_count,1)
            ledger=watch.load(self.directory,'notification.json');ledger['sentAt']-=25*3600
            watch.save(self.directory,'notification.json',ledger)
            repeat=watch.check(self.recipe,'source',self.settings,lambda:stale,True)
            self.assertEqual(repeat['notification']['state'],'sent')
            recovered=watch.check(self.recipe,'source',self.settings,self.inventory,True)
            self.assertEqual(recovered['notification']['state'],'sent');self.assertFalse(recovered['alarm'])
            watch.check(self.recipe,'source',self.settings,self.inventory,True)
            self.assertEqual(send.call_count,3)
        with patch('subprocess.run',side_effect=OSError),contextlib.redirect_stderr(io.StringIO()):
            failed=watch.check(self.recipe,'source',self.settings,lambda:stale,True)
        self.assertEqual(failed['notification']['state'],'failed')
        self.assertTrue(watch.load(self.directory,'latest.json')['alarm'])

    def test_unsafe_or_malformed_state_cannot_be_followed_or_silently_replaced(self):
        self.directory.mkdir(parents=True,mode=0o700)
        outside=self.root/'outside';outside.write_text('preserve');outside.chmod(0o600)
        (self.directory/'last-run.json').symlink_to(outside)
        report=watch.check(self.recipe,'source',self.settings,self.inventory)
        self.assertIn('invalid_state',report['issues']);self.assertEqual(outside.read_text(),'preserve')
        with self.assertRaises(ValueError):
            with watch.capture_run(self.directory,'source'):pass
        self.assertEqual(outside.read_text(),'preserve')
        (self.directory/'latest.json').symlink_to(outside)
        with contextlib.redirect_stderr(io.StringIO()):
            applied=watch.check(self.recipe,'source',self.settings,self.inventory,True)
        self.assertIn('state_write_failed',applied['issues']);self.assertFalse(applied['recorded'])
        self.assertEqual(outside.read_text(),'preserve')

    def test_monitor_bundle_never_captures_or_prunes(self):
        policy=backups.policy({'backup':{'enable':True,'provider':'configurations/server/provider.py',
             'repository':self.settings['repository'],'identityFile':str(self.root/'missing-key'),
             'recipient':'age1'+'a'*58,'keepLast':7,'monitor':{'enable':True}}})
        directory=self.root/'units'
        report=backups.schedule(self.recipe,'source',policy,directory,False,monitor=True)
        service=(directory/'nixberry-backup-monitor-source.service').read_text()
        self.assertIn('backup-check',service);self.assertNotIn('backup-prune',service)
        self.assertNotIn(' backup ',next(line for line in service.splitlines() if line.startswith('ExecStart=')))
        self.assertFalse(report['enabled'])
        self.assertIn('OnActiveSec=2min',(directory/'nixberry-backup-monitor-source.timer').read_text())

    def test_cli_health_alarm_has_nonzero_status_with_valid_json(self):
        declaration={'source':{'backend':'libvirt','host':'lab','spec':{},'backup':{
            'enable':True,'provider':'configurations/server/provider.py','repository':self.settings['repository'],
            'identityFile':str(self.root/'missing-key'),'recipient':'age1'+'a'*58}}}
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps(declaration))
        with patch.dict(os.environ,{'NIXBERRY_DEPLOYMENT':str(manifest),'NIXBERRY_RECIPE':str(self.recipe)}), \
                patch('backups.invoke',return_value={'snapshots':[]}),contextlib.redirect_stdout(io.StringIO()) as output:
            result=main(['backup-check','source','--json'])
        self.assertEqual(result,1);self.assertTrue(json.loads(output.getvalue())['alarm'])

    def test_inventory_timeout_is_a_health_finding(self):
        def timeout(): raise subprocess.TimeoutExpired('fixture',60)
        report=watch.check(self.recipe,'source',self.settings,timeout)
        self.assertIn('inventory_unavailable',report['issues']);self.assertTrue(report['alarm'])
