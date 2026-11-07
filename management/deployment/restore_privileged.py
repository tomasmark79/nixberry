"""Privileged filesystem publication checks, run only in a disposable NixOS VM."""
import os
from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

import restore_apply
from test_restore_apply import RestoreApplyFixture


class PrivilegedRestoreApplyTests(RestoreApplyFixture):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0:
            raise RuntimeError('Run privileged restore checks in the restore-publication NixOS VM.')

    def test_whiteout_publication_preserves_numeric_ownership_and_metadata(self):
        original = restore_apply.restore_payload.stage.side_effect
        def stage(*args):
            report = original(*args)
            whiteout = Path(report['paths'][0]['staged']) / 'removed'
            os.mknod(whiteout, stat.S_IFCHR | 0o640, os.makedev(0, 0))
            os.chmod(whiteout, 0o640)
            os.chown(whiteout, 123456, 234567)
            os.setxattr(whiteout, 'trusted.nixberry-test', b'whiteout metadata')
            os.utime(whiteout, ns=(1700000000123456789, 1700000000123456789))
            return report
        with patch('restore_apply.restore_payload.stage', side_effect=stage):
            result = self.run_restore(apply=True)
        details = (self.app / 'removed').lstat()
        self.assertEqual(result['state'], 'recorded-restored')
        self.assertEqual((details.st_mode, details.st_rdev, details.st_uid, details.st_gid, details.st_mtime_ns),
                         (stat.S_IFCHR | 0o640, os.makedev(0, 0), 123456, 234567, 1700000000123456789))
        self.assertEqual(os.getxattr(self.app / 'removed', 'trusted.nixberry-test'), b'whiteout metadata')
        self.assertEqual(self.units, ['started'])

    def test_other_character_and_block_devices_are_rejected_before_publication(self):
        original = restore_apply.restore_payload.stage.side_effect
        for kind, device in ((stat.S_IFCHR, os.makedev(0, 1)), (stat.S_IFCHR, os.makedev(1, 0)),
                             (stat.S_IFCHR, os.makedev(1, 3)), (stat.S_IFBLK, os.makedev(0, 0)),
                             (stat.S_IFBLK, os.makedev(8, 0))):
            with self.subTest(kind=kind, device=device):
                def stage(*args):
                    report = original(*args)
                    os.mknod(Path(report['paths'][0]['staged']) / 'device', kind | 0o600, device)
                    return report
                with patch('restore_apply.restore_payload.stage', side_effect=stage), self.assertRaises(ValueError):
                    self.run_restore(apply=True)
                self.assertFalse(self.app.exists())
                self.assertFalse(restore_apply.restore_target.marker_path(self.value).exists())
                self.assertEqual(self.units, [])

    def test_nested_different_device_and_same_device_bind_mount_disable_link_dest_for_entire_batch(self):
        for kind in ('tmpfs', 'bind'):
            with self.subTest(kind=kind):
                target = self.root / ('application-' + kind)
                nested = target / 'nested'
                nested.mkdir(parents=True)
                source = self.root / 'view' / str(target).removeprefix('/')
                (source / 'nested').mkdir(parents=True)
                (source / 'top').write_text('same-mount data')
                (source / 'nested/example').write_text('nested data')
                os.link(source / 'nested/example', source / 'nested/hardlink')
                (source / 'nested/symlink').symlink_to('example')
                if kind == 'tmpfs':
                    command = ['mount', '-t', 'tmpfs', '-o', 'size=8m', 'tmpfs', str(nested)]
                else:
                    backing = self.root / 'bind-backing'
                    backing.mkdir()
                    command = ['mount', '--bind', str(backing), str(nested)]
                subprocess.run(command, check=True)
                try:
                    expected = restore_apply.tree_hash(self.root / 'view', [target])
                    restore_apply.transfer(self.root / 'view', [target], self.root)
                    restore_apply.transfer(self.root / 'view', [target], self.root, check=True)
                    self.assertEqual(restore_apply.tree_hash(Path('/'), [target]), expected)
                    self.assertNotEqual((target / 'top').stat().st_ino, (source / 'top').stat().st_ino)
                    self.assertEqual((nested / 'example').stat().st_ino, (nested / 'hardlink').stat().st_ino)
                    self.assertEqual(os.readlink(nested / 'symlink'), 'example')
                finally:
                    subprocess.run(['umount', str(nested)], check=True)
