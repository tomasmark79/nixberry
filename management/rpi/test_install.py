#!/usr/bin/env python3
"""Ochrany cíle RPi a systémového disku před instalačním zápisem."""

from copy import deepcopy
from contextlib import nullcontext
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

API = runpy.run_path(str(Path(__file__).with_name("install.py")))
TARGET = {
    "target": "test", "hardwareSerial": "0123456789abcdef",
    "lanMac": "02:00:00:00:00:20", "diskSerial": "TARGET",
    "minimumDiskBytes": 1000, "systemPath": "/nix/store/system",
    "diskoPath": "/nix/store/disko", "bootLabel": "test-boot", "rootLabel": "test-root",
}
SNAPSHOT = {
    "architecture": "aarch64", "model": "Raspberry Pi 5 Model B",
    "serial": TARGET["hardwareSerial"], "links": [{"address": TARGET["lanMac"]}],
    "device": "/dev/sda",
    "disks": [{"path": "/dev/sda", "type": "disk", "size": 2000,
               "serial": "TARGET", "mountpoints": [], "children": []}],
    "installer": {"test": TARGET}, "signatures": {"signatures": []},
}


class RpiChecks(unittest.TestCase):
    def test_recovery_ssh_preserves_literal_temporary_host_key_path(self):
        execute = subprocess.run

        class ProbeInspected(Exception):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'recovery "quoted" %h'
            state.mkdir()
            probe = root / 'probe.sh'
            probe.write_text('exit 0\n')
            declarations = root / 'targets.json'
            for encrypted in (False, True):
                with self.subTest(encrypted=encrypted):
                    target = {**TARGET, 'installerAddress': '192.168.50.20', 'diskDevice': '/dev/sda',
                              'security': {'mode': 'luks' if encrypted else 'none',
                                           'recoveryHostKey': 'ssh-ed25519 TESTKEY'}}
                    declarations.write_text(json.dumps({'test': target}))

                    def inspect(command, **kwargs):
                        result = execute(['ssh', '-F', '/dev/null', '-G', *command[1:-1]],
                                         text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        actual = next((line.removeprefix('userknownhostsfile ')
                                       for line in result.stdout.splitlines()
                                       if line.startswith('userknownhostsfile ')), None)
                        self.assertEqual(actual, str(state / 'known_hosts'))
                        strict = 'true' if encrypted else 'accept-new'
                        self.assertIn('stricthostkeychecking ' + strict, result.stdout.splitlines())
                        raise ProbeInspected

                    with patch.dict(os.environ, {'NIXBERRY_RPI_TARGETS': str(declarations),
                                                 'NIXBERRY_RPI_PROBE': str(probe)}), \
                            patch.object(sys, 'argv', ['install.py', '--target', 'test', '--dry-run']), \
                            patch('tempfile.TemporaryDirectory', return_value=nullcontext(str(state))), \
                            patch('subprocess.run', side_effect=inspect), self.assertRaises(ProbeInspected):
                        API['main']()

    def test_encrypted_policy_and_host_keys_must_match_the_prepared_medium(self):
        target, snapshot = deepcopy(TARGET), deepcopy(SNAPSHOT)
        target["security"] = {"mode": "luks", "initrdHostKey": "ssh-ed25519 EXPECTED"}
        with self.assertRaisesRegex(RuntimeError, "different RPi encryption policy"):
            API["validate"](snapshot, target, installation=True)
        snapshot["installer"]["test"]["security"] = target["security"]
        API["validate"](snapshot, target, installation=True)

    def test_flat_lsblk_output_rejected(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["disks"].append({"path": "/dev/sda2", "type": "part", "mountpoints": ["/"]})
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True, erase_existing=True)

    def test_clean_target(self):
        API["validate"](SNAPSHOT, TARGET, installation=True)

    def test_stage_allows_current_system_disk(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["installer"] = {}
        snapshot["disks"][0]["children"] = [{"path": "/dev/sda2", "type": "part", "mountpoints": ["/"]}]
        API["validate"](snapshot, TARGET, installation=False)
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True, erase_existing=True)

    def test_installer_required(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["installer"] = {}
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True)

    def test_mounted_disk_never_erasable(self):
        for mounts in (["/"], ["/boot"], ["/nix/.ro-store"], ["[SWAP]"]):
            with self.subTest(mounts=mounts):
                snapshot = deepcopy(SNAPSHOT)
                snapshot["disks"][0]["mountpoints"] = mounts
                for erase in (False, True):
                    with self.assertRaises(RuntimeError):
                        API["validate"](snapshot, TARGET, installation=True, erase_existing=erase)

    def test_existing_data_requires_explicit_erasure(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["disks"][0]["children"] = [
            {"path": "/dev/sda2", "type": "part", "partlabel": "test-root", "mountpoints": []}]
        snapshot["signatures"]["signatures"] = [{"type": "gpt"}]
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True)
        API["validate"](snapshot, TARGET, installation=True, erase_existing=True)

    def test_foreign_label_collision_rejected(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["disks"].append({"path": "/dev/sdb", "type": "disk", "children": [
            {"path": "/dev/sdb2", "type": "part", "partlabel": "test-root"}]})
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True, erase_existing=True)

    def test_mapped_devices_rejected(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["disks"][0]["children"] = [
            {"path": "/dev/mapper/data", "type": "lvm", "mountpoints": []}]
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True, erase_existing=True)

    def test_wrong_hardware_or_disk(self):
        for key, value in (("architecture", "x86_64"), ("model", "Raspberry Pi 4"),
                           ("serial", "foreign"), ("device", "/dev/sdb")):
            with self.subTest(key=key):
                snapshot = deepcopy(SNAPSHOT)
                snapshot[key] = value
                with self.assertRaises(RuntimeError):
                    API["validate"](snapshot, TARGET, installation=False)
        for key, value in (("serial", "foreign"), ("size", 999)):
            with self.subTest(key=key):
                snapshot = deepcopy(SNAPSHOT)
                snapshot["disks"][0][key] = value
                with self.assertRaises(RuntimeError):
                    API["validate"](snapshot, TARGET, installation=False)

    def test_signatures_rejected(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["signatures"]["signatures"] = [{"type": "ext4"}]
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True)

    def test_wrong_installer_outputs(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["installer"]["test"]["systemPath"] = "/nix/store/other-system"
        with self.assertRaises(RuntimeError):
            API["validate"](snapshot, TARGET, installation=True)

    def test_address_boundary(self):
        self.assertEqual(API["validate_address"]("192.168.50.20"), "192.168.50.20")
        for address in ("192.168.79.1", "127.0.0.1", "::1", "8.8.8.8", "0.0.0.0", "169.254.1.2"):
            with self.subTest(address=address):
                with self.assertRaises(RuntimeError):
                    API["validate_address"](address)

    def test_incomplete_probe(self):
        with self.assertRaises(RuntimeError):
            API["parse_probe"]("partial response")


if __name__ == "__main__":
    unittest.main()
