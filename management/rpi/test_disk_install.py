"""Direct SSD installation rejects unsafe local disks before any write."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


class DiskSafetyTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name("disk_install.py")
        self.assertTrue(path.exists(), "Local SSD installation is not implemented")
        spec = importlib.util.spec_from_file_location("disk_install", path)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.target = {"diskSerial": "SSD-TEST", "minimumDiskBytes": 1000,
                       "bootLabel": "test-boot", "rootLabel": "test-root"}
        self.snapshot = {"device": "/dev/sdz", "diskseq": 42, "signatures": [], "holders": [],
                         "busy": [], "disks": [{"path": "/dev/sdz", "type": "disk",
                         "size": 2000, "serial": "SSD-TEST", "ro": False,
                         "maj:min": "65:144", "mountpoints": [], "children": []}]}

    def validate(self, *, erase=False, snapshot=None, target=None):
        return self.api.validate_disk(snapshot or self.snapshot, target or self.target,
                                      erase_existing=erase)

    def test_clean_physical_disk(self):
        self.assertEqual(self.validate()["path"], "/dev/sdz")

    def test_no_missing_or_mismatched_serial(self):
        for serial in (None, "", "OTHER"):
            with self.subTest(serial=serial):
                self.snapshot["disks"][0]["serial"] = serial
                with self.assertRaises(RuntimeError):
                    self.validate(erase=True)

    def test_readonly_small_or_nonphysical_disks_rejected(self):
        for key, value in (("ro", True), ("size", 999), ("type", "loop"), ("type", "part")):
            snapshot = deepcopy(self.snapshot)
            snapshot["disks"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                self.validate(erase=True, snapshot=snapshot)

    def test_mounts_swap_holders_and_other_namespace_mounts_rejected(self):
        for field in ("mount", "swap", "holder", "namespace"):
            snapshot = deepcopy(self.snapshot)
            if field in ("mount", "swap"):
                snapshot["disks"][0]["children"] = [{"type": "part", "path": "/dev/sdz1",
                    "mountpoints": ["/home" if field == "mount" else "[SWAP]"]}]
            elif field == "holder":
                snapshot["holders"] = ["dm-0"]
            else:
                snapshot["busy"] = ["65:145"]
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate(erase=True, snapshot=snapshot)

    def test_existing_data_requires_erase_existing(self):
        self.snapshot["signatures"] = [{"type": "gpt"}]
        self.snapshot["disks"][0]["children"] = [{"type": "part", "path": "/dev/sdz1"}]
        with self.assertRaises(RuntimeError):
            self.validate()
        self.validate(erase=True)

    def test_mapped_children_rejected_even_with_erase(self):
        self.snapshot["disks"][0]["children"] = [{"type": "crypt", "path": "/dev/dm-0"}]
        with self.assertRaises(RuntimeError):
            self.validate(erase=True)

    def test_foreign_labels_rejected(self):
        self.snapshot["disks"].append({"path": "/dev/sdy", "type": "disk", "children": [
            {"path": "/dev/sdy1", "type": "part", "partlabel": "test-root"}]})
        with self.assertRaises(RuntimeError):
            self.validate(erase=True)

    def test_local_alias_must_be_whole_disk_by_id(self):
        for path in ("/dev/sda", "/dev/disk/by-id/usb-TEST-part1", "relative", "/dev/disk/by-id/../sda"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.api.validate_local_path(path)
        self.assertEqual(self.api.validate_local_path("/dev/disk/by-id/usb-TEST"),
                         "/dev/disk/by-id/usb-TEST")

    def test_encrypted_local_target_fails_before_disk_access(self):
        with patch.object(self.api.Path, "read_text", return_value='{"security":{"mode":"luks"}}'), \
                patch.object(self.api, "probe", side_effect=AssertionError("must not inspect disk")):
            with self.assertRaisesRegex(RuntimeError, "unencrypted RPi targets only"):
                self.api.main(["--manifest", "/test.json", "--local-disk", "/dev/disk/by-id/usb-TEST", "--apply"])

    def test_changed_disk_identity_fails_before_mount_or_write(self):
        expected = ["/dev/sdz", "65:144", 2000, "SSD-TEST", 42]
        snapshot = deepcopy(self.snapshot)
        snapshot["disks"][0]["size"] = 3000
        # An already isolated test namespace avoids granting mount privileges.
        original_stat = self.api.os.stat
        def fake_stat(path, *args, **kwargs):
            if str(path) in ("/proc/self/ns/mnt", "/proc/1/ns/mnt"):
                return type("S", (), {"st_ino": 2 if "self" in str(path) else 1})()
            return original_stat(path, *args, **kwargs)
        with patch.object(self.api.os, "stat", side_effect=fake_stat), \
                patch.object(self.api, "probe", return_value=snapshot), \
                patch.object(self.api.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                self.api.install(self.target, "/dev/disk/by-id/usb-TEST", erase_existing=True, expected=expected)
            run.assert_not_called()

    def test_reconnected_disk_with_same_name_and_serial_has_different_identity(self):
        original = self.api.disk_identity(self.snapshot, self.snapshot["disks"][0])
        self.snapshot["diskseq"] += 1
        self.assertNotEqual(original, self.api.disk_identity(self.snapshot, self.snapshot["disks"][0]))

    def test_partition_paths_are_children_of_the_held_disk_not_global_labels(self):
        self.assertEqual(self.api.partition_paths("/dev/sdz"), ["/dev/sdz1", "/dev/sdz2"])
        self.assertEqual(self.api.partition_paths("/dev/nvme1n1"), ["/dev/nvme1n1p1", "/dev/nvme1n1p2"])


if __name__ == "__main__":
    unittest.main()
