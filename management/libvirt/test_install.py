#!/usr/bin/env python3
"""Ověření odmítnutí cizího disku, identity a adresy před instalací."""

from pathlib import Path
import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service_test import assert_services

MODULE = runpy.run_path(str(Path(__file__).with_name("install.py")))
validate_domain = MODULE["validate_domain"]
choose_address = MODULE["choose_address"]
MANIFEST = {
    "name": "nixberry-repro-test",
    "uuid": "315b9a43-1653-4b2f-9cd9-790081000001",
    "diskPath": "/var/lib/libvirt/images/nixberry-repro-test.qcow2",
    "isoPath": "/nix/store/test/installer.iso",
    "mac": "52:54:00:79:81:01",
    "network": "default",
}
DOMAIN = """<domain><name>nixberry-repro-test</name>
<uuid>315b9a43-1653-4b2f-9cd9-790081000001</uuid><devices>
<disk device="disk"><source file="/var/lib/libvirt/images/nixberry-repro-test.qcow2"/>
<target dev="vda"/></disk>
<disk device="cdrom"><source file="/nix/store/test/installer.iso"/><target dev="sda"/></disk>
<interface type="network"><mac address="52:54:00:79:81:01"/><source network="default"/></interface>
</devices></domain>"""
NETWORK = """<network><forward mode="nat"/><ip address="192.168.77.1"
netmask="255.255.255.0"/></network>"""


class InstallChecks(unittest.TestCase):
    def test_verified_key_survives_temporary_installer_state(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_STATE_HOME": directory}):
            source = Path(directory) / "temporary-known-hosts"
            source.write_text("192.168.77.97 ssh-ed25519 verified-key\n")
            MODULE["remember_host_key"](source, MANIFEST)
            source.unlink()
            destination = Path(directory) / "nixberry/portable" / MANIFEST["uuid"] / "known_hosts"
            self.assertEqual(destination.read_text(), "192.168.77.97 ssh-ed25519 verified-key\n")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_image_allows_empty_optical_drive_but_rejects_attached_installer(self):
        manifest = {**MANIFEST, "deploymentMethod": "image"}
        with self.assertRaisesRegex(RuntimeError, "installation ISO attached"):
            validate_domain(DOMAIN, manifest)
        root = ET.fromstring(DOMAIN)
        cd = root.find("./devices/disk[@device='cdrom']")
        cd.remove(cd.find("source"))
        validate_domain(ET.tostring(root, encoding="unicode"), manifest)

    def test_actual_installed_shell_requires_every_service_active(self):
        assert_services(self, MODULE["installed_check"]({**MANIFEST, "systemPath": "/nix/store/system"}),
                        ["sshd", "systemd-networkd", "qemu-guest-agent"])

    def run_shared_install(self, *, apply, attach_during_guest_check=False):
        manifest = {**MANIFEST, "uri": "qemu:///system", "diskGiB": 32,
                    "systemPath": "/nix/store/system", "diskoPath": "/nix/store/disko"}
        shared = not attach_during_guest_check
        commands = []

        def virsh(command, **kwargs):
            args = tuple(command[3:])
            commands.append(args)
            if args[0] == "domstate":
                return "running"
            if args[0] == "list":
                return manifest["name"] + "\nstopped-peer"
            if args[:2] == ("dumpxml", manifest["name"]):
                return DOMAIN
            if args[:2] == ("dumpxml", "stopped-peer"):
                path = manifest["diskPath"] if shared else "/other.qcow2"
                return '<domain><devices><disk><source file="' + path + '"/></disk></devices></domain>'
            if args[0] == "domifaddr":
                return "vnet0 52:54:00:79:81:01 ipv4 192.168.77.97/24"
            if args[0] == "net-dumpxml":
                return NETWORK
            raise AssertionError(args)

        def guest(*args, **kwargs):
            nonlocal shared
            shared = True
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest").write_text(json.dumps(manifest))
            (path / "guest").write_text("# zkušební guest prostředí")
            with patch.dict(os.environ, {"NIXBERRY_MANIFEST": str(path / "manifest"),
                                         "NIXBERRY_GUEST_INSTALL": str(path / "guest")}), \
                    patch.object(sys, "argv", ["install", "--apply" if apply else "--dry-run"]), \
                    patch.object(MODULE["policy"], "validate"), \
                    patch.object(subprocess, "check_output", side_effect=virsh), \
                    patch.object(subprocess, "run", side_effect=guest) as ssh, \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "shared with another VM"):
                MODULE["main"]()
            return commands, ssh.call_args_list

    def test_stopped_peer_shared_disk_prevents_plan_and_apply_before_ssh(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                commands, ssh_calls = self.run_shared_install(apply=apply)
                self.assertIn(("dumpxml", "stopped-peer"), commands)
                self.assertEqual(ssh_calls, [])

    def test_disk_attached_after_guest_plan_is_rejected_before_write(self):
        commands, ssh_calls = self.run_shared_install(apply=True, attach_during_guest_check=True)
        self.assertEqual(len(ssh_calls), 1)
        self.assertIn("dry-run", ssh_calls[0].args[0][-1])
        self.assertGreaterEqual(commands.count(("dumpxml", "stopped-peer")), 2)

    def test_expected_domain(self):
        validate_domain(DOMAIN, MANIFEST)

    def test_other_domain_or_disk_is_rejected(self):
        for element, attribute, value in (
            ("name", None, "broucek"),
            ("uuid", None, "other-uuid"),
            ("./devices/disk[@device='disk']/source", "file", "/dev/sda"),
            ("./devices/disk[@device='disk']/target", "dev", "sda"),
            ("./devices/disk[@device='cdrom']/source", "file", "/other.iso"),
            ("./devices/interface/mac", "address", "52:54:00:00:00:00"),
            ("./devices/interface/source", "network", "other-network"),
        ):
            with self.subTest(element=element):
                root = ET.fromstring(DOMAIN)
                if attribute:
                    root.find(element).set(attribute, value)
                else:
                    root.find(element).text = value
                with self.assertRaises(RuntimeError):
                    validate_domain(ET.tostring(root, encoding="unicode"), MANIFEST)

    def test_extra_disk_is_rejected(self):
        root = ET.fromstring(DOMAIN)
        ET.SubElement(root.find("devices"), "disk", {"device": "disk"})
        with self.assertRaises(RuntimeError):
            validate_domain(ET.tostring(root, encoding="unicode"), MANIFEST)

    def test_expected_address(self):
        self.assertEqual(choose_address("vnet0 52:54:00:79:81:01 ipv4 192.168.77.97/24",
                                       NETWORK, MANIFEST["mac"]), "192.168.77.97")

    def test_unsafe_addresses_are_rejected(self):
        for output in (
            "vnet0 52:54:00:79:81:01 ipv4 192.168.79.1/24",
            "vnet0 52:54:00:79:81:01 ipv4 192.168.77.1/24",
            "vnet0 52:54:00:00:00:00 ipv4 192.168.77.97/24",
            "vnet0 52:54:00:79:81:01 ipv4 192.168.77.97/24\n"
            "vnet0 52:54:00:79:81:01 ipv4 192.168.77.98/24",
        ):
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    choose_address(output, NETWORK, MANIFEST["mac"])

    def test_non_nat_network_is_rejected(self):
        with self.assertRaises(RuntimeError):
            choose_address("vnet0 52:54:00:79:81:01 ipv4 192.168.77.97/24",
                           NETWORK.replace('mode="nat"', 'mode="bridge"'), MANIFEST["mac"])


if __name__ == "__main__":
    unittest.main()
