#!/usr/bin/env python3
"""Ochrany odstranění testovacího cíle a pořadí jednotného vytvoření."""

import contextlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
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
