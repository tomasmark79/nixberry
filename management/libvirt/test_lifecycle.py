#!/usr/bin/env python3
"""Ochrany odstranění testovacího cíle a pořadí jednotného vytvoření."""

import contextlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

RESET = runpy.run_path(str(Path(__file__).with_name("reset.py")))
PROVISION = runpy.run_path(str(Path(__file__).with_name("provision.py")))
MANIFEST = {
    "name": "nixberry-repro-test",
    "uuid": "315b9a43-1653-4b2f-9cd9-790081000001",
    "diskPath": "/var/lib/libvirt/images/nixberry-repro-test.qcow2",
    "volume": "nixberry-repro-test.qcow2",
    "diskGiB": 32,
}
NVRAM = "/var/lib/libvirt/qemu/nvram/nixberry-repro-test_VARS.fd"
DOMAIN = """<domain><name>nixberry-repro-test</name>
<uuid>315b9a43-1653-4b2f-9cd9-790081000001</uuid>
<os><nvram>/var/lib/libvirt/qemu/nvram/nixberry-repro-test_VARS.fd</nvram></os>
<devices><disk device="disk">
<source file="/var/lib/libvirt/images/nixberry-repro-test.qcow2"/>
<target dev="vda"/></disk></devices></domain>"""
VOLUME = """<volume><name>nixberry-repro-test.qcow2</name>
<capacity unit="bytes">34359738368</capacity><target>
<path>/var/lib/libvirt/images/nixberry-repro-test.qcow2</path>
<format type="qcow2"/></target></volume>"""


class ResetChecks(unittest.TestCase):
    def test_expected_target(self):
        self.assertEqual(RESET["validate_target"](DOMAIN, VOLUME, MANIFEST),
                         (MANIFEST["diskPath"], NVRAM))

    def test_wrong_identity_disk_or_nvram(self):
        for element, attribute, value in (
            ("name", None, "another-vm"),
            ("uuid", None, "other-uuid"),
            ("./devices/disk/source", "file", "/another.qcow2"),
            ("./devices/disk/target", "dev", "sda"),
            ("./os/nvram", None, "/var/lib/libvirt/qemu/nvram/another_VARS.fd"),
        ):
            with self.subTest(element=element):
                root = ET.fromstring(DOMAIN)
                if attribute:
                    root.find(element).set(attribute, value)
                else:
                    root.find(element).text = value
                with self.assertRaises(RuntimeError):
                    RESET["validate_target"](ET.tostring(root, encoding="unicode"), VOLUME, MANIFEST)

    def test_wrong_volume(self):
        for element, attribute, value in (
            ("name", None, "another.qcow2"),
            ("target/path", None, "/another.qcow2"),
            ("capacity", None, "1"),
            ("target/format", "type", "raw"),
        ):
            with self.subTest(element=element):
                root = ET.fromstring(VOLUME)
                if attribute:
                    root.find(element).set(attribute, value)
                else:
                    root.find(element).text = value
                with self.assertRaises(RuntimeError):
                    RESET["validate_target"](DOMAIN, ET.tostring(root, encoding="unicode"), MANIFEST)

    def test_extra_disk_and_backing_store(self):
        root = ET.fromstring(DOMAIN)
        ET.SubElement(root.find("devices"), "disk", {"device": "disk"})
        with self.assertRaises(RuntimeError):
            RESET["validate_target"](ET.tostring(root, encoding="unicode"), VOLUME, MANIFEST)
        volume = ET.fromstring(VOLUME)
        ET.SubElement(ET.SubElement(volume, "backingStore"), "path").text = "/base.qcow2"
        with self.assertRaises(RuntimeError):
            RESET["validate_target"](DOMAIN, ET.tostring(volume, encoding="unicode"), MANIFEST)

    def test_shared_disk_and_nvram(self):
        for xml in (
            DOMAIN,
            "<domain><os><nvram>" + NVRAM + "</nvram></os></domain>",
            '<domain><devices><disk><backingStore><source file="' +
            MANIFEST["diskPath"] + '"/></backingStore></disk></devices></domain>',
        ):
            with self.subTest(xml=xml):
                with self.assertRaises(RuntimeError):
                    RESET["reject_shared"](xml, MANIFEST["diskPath"], NVRAM)

    def test_unrelated_peer(self):
        RESET["reject_shared"](
            '<domain><devices><disk><source file="/another.qcow2"/></disk></devices></domain>',
            MANIFEST["diskPath"], NVRAM)


class ResetFileChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.disk = self.root / "disk.qcow2"
        self.disk.write_bytes(b"fixture")

    def test_regular_file_is_accepted_without_changes(self):
        RESET["validate_file"](self.disk)
        self.assertEqual(self.disk.read_bytes(), b"fixture")

    def test_linked_storage_directory_records_resolved_file_and_link(self):
        link = self.root / "images"
        link.symlink_to(self.root, target_is_directory=True)
        result = RESET["validate_file"](link / self.disk.name)
        self.assertEqual(result["resolved"], str(self.disk))
        self.assertEqual(result["links"], ((str(link), str(self.root)),))
        self.assertEqual(result["identity"], (self.disk.stat().st_dev, self.disk.stat().st_ino))
        self.assertEqual(self.disk.read_bytes(), b"fixture")

    def test_leaf_link_is_rejected_even_when_broken(self):
        for target in (self.disk, self.root / "absent"):
            link = self.root / "linked.qcow2"
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "symbolic link") as failure:
                RESET["validate_file"](link)
            self.assertIn(str(link), str(failure.exception))
            link.unlink()

    def test_missing_and_nonregular_paths_have_specific_errors(self):
        missing = self.root / "absent"
        with self.assertRaisesRegex(RuntimeError, "Cannot inspect reset path") as failure:
            RESET["validate_file"](missing)
        self.assertIn(str(missing), str(failure.exception))
        with self.assertRaisesRegex(RuntimeError, "not a regular file"):
            RESET["validate_file"](self.root)

    def test_hardlinked_file_is_rejected_without_removing_either_name(self):
        alias = self.root / "shared.qcow2"
        os.link(self.disk, alias)
        with self.assertRaisesRegex(RuntimeError, "multiple hard links") as failure:
            RESET["validate_file"](self.disk)
        self.assertIn(str(self.disk), str(failure.exception))
        self.assertEqual(alias.read_bytes(), b"fixture")

    def test_mount_point_is_rejected(self):
        with patch.object(Path, "is_mount", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "mount point"):
                RESET["validate_file"](self.disk)


class ProvisionChecks(unittest.TestCase):
    def test_default_only_checks_creation(self):
        with patch.dict(os.environ, {"NIXBERRY_CREATE": "create", "NIXBERRY_INSTALL": "install"}), \
             patch.object(sys, "argv", ["provision"]), patch.object(subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            PROVISION["main"]()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ["create", "--dry-run"])

    def test_apply_waits_before_installation(self):
        calls = []
        def execute(arguments, **kwargs):
            calls.append(arguments)
            if arguments == ["install", "--dry-run"]:
                return SimpleNamespace(returncode=0 if len(calls) >= 3 else 1,
                                       stdout="", stderr="not ready")
            return SimpleNamespace(returncode=0)
        with patch.dict(os.environ, {"NIXBERRY_CREATE": "create", "NIXBERRY_INSTALL": "install"}), \
             patch.object(sys, "argv", ["provision", "--apply"]), \
             patch.object(subprocess, "run", side_effect=execute), \
             patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            PROVISION["main"]()
        self.assertEqual(calls, [["create", "--apply"], ["install", "--dry-run"],
                                 ["install", "--dry-run"], ["install", "--apply"]])


if __name__ == "__main__":
    unittest.main()
