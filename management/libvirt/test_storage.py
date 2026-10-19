"""Sdílené disky v živých i trvalých doménách, včetně aliasů cest a svazků."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from storage import reject_shared, reject_shared_domains


def domain(source):
    return '<domain><devices><disk device="disk">' + source + '</disk></devices></domain>'


class StorageChecks(unittest.TestCase):
    def test_symlink_hardlink_block_device_and_backing_store_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            disk = Path(directory) / "disk"
            disk.touch()
            symlink = Path(directory) / "symlink"
            symlink.symlink_to(disk)
            hardlink = Path(directory) / "hardlink"
            hardlink.hardlink_to(disk)
            for alias in (disk, symlink, hardlink):
                for source in (f'<source file="{alias}"/>', f'<source dev="{alias}"/>',
                               f'<backingStore><source file="{alias}"/></backingStore>'):
                    with self.subTest(source=source), self.assertRaises(RuntimeError):
                        reject_shared(domain(source), str(disk))

    def test_volume_alias_is_resolved_and_rejected(self):
        virsh = Mock(return_value="/disk")
        with self.assertRaises(RuntimeError):
            reject_shared(domain('<source pool="default" volume="alias"/>'), "/disk", virsh=virsh)
        virsh.assert_called_once_with("vol-path", "alias", "--pool", "default")

    def test_unresolved_volume_fails_closed(self):
        with self.assertRaises(RuntimeError):
            reject_shared(domain('<source pool="default" volume="alias"/>'), "/disk")

    def test_nvram_and_unrelated_disk(self):
        reject_shared(domain('<source file="/other"/>'), "/disk", "/nvram")
        with self.assertRaises(RuntimeError):
            reject_shared('<domain><os><nvram>/nvram</nvram></os></domain>', "/disk", "/nvram")

    def test_stopped_persistent_peer_and_live_only_peer_are_both_checked(self):
        calls = []

        def virsh(*args):
            calls.append(args)
            if args == ("list", "--all", "--persistent", "--name"):
                return "target\nstopped"
            if args == ("list", "--all", "--name"):
                return "target\nstopped\ntransient"
            return domain('<source file="/other"/>')

        reject_shared_domains(virsh, "target", "/disk")
        self.assertIn(("dumpxml", "stopped"), calls)
        self.assertIn(("dumpxml", "stopped", "--inactive"), calls)
        self.assertIn(("dumpxml", "transient"), calls)
        self.assertNotIn(("dumpxml", "transient", "--inactive"), calls)

    def test_persistent_xml_may_differ_from_running_xml(self):
        def virsh(*args):
            if args[0] == "list":
                return "target\npeer"
            return domain('<source file="' + ("/disk" if "--inactive" in args else "/other") + '"/>')
        with self.assertRaises(RuntimeError):
            reject_shared_domains(virsh, "target", "/disk")
