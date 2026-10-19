#!/usr/bin/env python3
"""Ochrany automatické volby adresy, identity VM a SSH připojení."""

import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import runtime
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service_test import assert_services

LIBVIRT = {"backend": "libvirt", "spec": {
    "uri": "qemu:///system", "name": "test", "uuid": "expected", "mac": "52:54:00:79:81:01"}}
DOMAIN = ('<domain><name>test</name><uuid>expected</uuid><devices>'
          '<interface><mac address="52:54:00:79:81:01"/></interface></devices></domain>')
LEASES = ('Name MAC Protocol Address\nvnet0 52:54:00:79:81:01 ipv4 192.168.77.97/24\n'
          'vnet1 52:54:00:79:81:99 ipv4 192.168.79.1/24')
HYPERV = {"backend": "hyperv", "commands": {"probe": "/probe"}, "spec": {
    "name": "test", "windowsComputerName": "WINDOWS-DEMO", "generation": 2,
    "mac": "027981000002", "switchName": "Default Switch",
    "sshHost": "192.168.50.10", "sshUser": "operator", "ownerId": "test-owner"}}
WINDOWS = {"computerName": "WINDOWS-DEMO", "hypervModule": True, "hypervError": None,
           "vms": [{"name": "test", "ownerId": "test-owner", "generation": 2, "state": "Running", "network": [
               {"mac": "027981000002", "switchName": "Default Switch",
                "ips": ["172.21.222.76", "fe80::2", "192.168.50.10"]}]}]}
RPI = {"backend": "rpi", "spec": {"address": "192.168.50.20/24",
    "hardwareSerial": "0000000000000000", "lanMac": "02:00:00:00:00:20"}}
RPI_OUTPUT = ('aarch64\n0000000000000000\nnixberry\n'
              '[{"address":"02:00:00:00:00:20"}]\n/dev/sda2')


class RuntimeChecks(unittest.TestCase):
    def test_ssh_warning_does_not_hide_failed_remote_check_on_stdout(self):
        result = SimpleNamespace(returncode=1, stderr="SSH warning\n", stdout="Kontrola neprošla: UUID\n")
        with patch.object(runtime.subprocess, "run", return_value=result), self.assertRaises(RuntimeError) as error:
            runtime.read(["ssh", "guest", "test"])
        self.assertIn("SSH warning", str(error.exception))
        self.assertIn("Kontrola neprošla: UUID", str(error.exception))

    def test_generation_mismatch_reports_expected_and_running_system_before_other_checks(self):
        target = {**LIBVIRT, "guestHostName": "nixberry-vm"}
        with patch.object(runtime, "status", return_value={"state": "running", "addresses": ["10.0.0.2"]}), \
                patch.object(runtime, "read", return_value="nixberry-vm\n/nix/store/expected\n/dev/vda2") as read:
            runtime.verify(target, "/nix/store/expected")
        script = read.call_args.args[0][-1]
        result = subprocess.run(["sh", "-c", "readlink() { printf '/nix/store/actual\\n'; }; " + script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("The running system does not match the recipe.", result.stdout)
        self.assertIn("Expected system: /nix/store/expected", result.stdout)
        self.assertIn("Running system: /nix/store/actual", result.stdout)

    def test_direct_ssh_endpoint_validates_syntax_and_port(self):
        self.assertEqual(runtime.guest_connection("root@203.0.113.19:2030"),
                         {"host": "203.0.113.19", "port": 2030, "user": "root"})
        self.assertEqual(runtime.guest_connection("root@server.example")["port"], 22)
        for endpoint in ("user@host", "root@host:0", "root@host:65536", "root@999.1.1.1", "root@host;id",
                         "root@-option", "root@host\n", "root@a..b", "root@host:22 -J other"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                runtime.guest_connection(endpoint)

    def test_direct_offline_vm_checks_hostname_and_uses_public_endpoint(self):
        target = {"backend": "proxmox", "offline": True, "guestHostName": "nixberry-kozlonix",
                  "spec": {"uuid": "operator-uuid", "mac": "operator-mac"},
                  "_guestConnection": runtime.guest_connection("root@203.0.113.19:2030")}
        with patch.object(runtime, "read", return_value="x86_64\nnixberry-kozlonix\n/dev/vda2") as read:
            snapshot = runtime.status(target)
        command = read.call_args.args[0]
        self.assertEqual(snapshot["addresses"], ["203.0.113.19"])
        self.assertEqual(command[command.index("-p") + 1], "2030")
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertNotIn("-J", command)
        with patch.object(runtime, "read", return_value="x86_64\nforeign\n/dev/vda2"), \
                self.assertRaisesRegex(RuntimeError, "hostname"):
            runtime.status(target)
        manual = "\n".join(runtime.installation_checks(target))
        self.assertIn("disk-system-root", manual)
        self.assertIn("disk-system-boot", manual)
        self.assertNotIn("product_uuid", manual)
        managed = "\n".join(runtime.installation_checks({**target, "offline": False}))
        self.assertIn("product_uuid", managed)
        self.assertIn("operator-mac", managed)

    def test_every_offline_backend_uses_declared_access_without_hypervisor(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            with self.subTest(backend=backend):
                target = {"backend": backend, "offline": True, "guestHostName": "offline-vm", "spec": {},
                          "access": {"host": "server.example", "port": 2030}}
                self.assertEqual(runtime.capabilities(target),
                                 {"build": True, "export": True, "system": True, "hypervisor": False})
                with patch.object(runtime, "read", return_value="x86_64\noffline-vm\n/dev/sda2") as read:
                    runtime.status(target)
                command = read.call_args.args[0]
                self.assertIn("root@server.example", command)
                self.assertNotIn("-J", command)
                self.assertEqual(command[command.index("-p") + 1], "2030")

    def test_manual_image_checks_accept_both_controllers_and_reject_wrong_partitions(self):
        target = {"backend": "proxmox", "offline": True, "spec": {}, "_guestConnection": {"host": "test"}}
        checks = [check for check in runtime.installation_checks(target)
                  if "by-partlabel" in check or "lsblk" in check]
        self.assertEqual(len(checks), 4)
        fixtures = r'''
findmnt() { case "$*" in *'SOURCE /boot') echo "$BOOTDEV";; *'SOURCE /') echo "$ROOTDEV";; esac; }
readlink() { case "$*" in *disk-system-root) echo "$ROOTLABEL";; *disk-system-boot) echo "$BOOTLABEL";; *) echo "${@: -1}";; esac; }
lsblk() { case "${@: -1}" in "$ROOTDEV") echo "$ROOTPARENT";; "$BOOTDEV") echo "$BOOTPARENT";; esac; }
'''
        for disk in ("sda", "vda"):
            env = {**os.environ, "ROOTDEV": "/dev/" + disk + "2", "BOOTDEV": "/dev/" + disk + "1",
                   "ROOTLABEL": "/dev/" + disk + "2", "BOOTLABEL": "/dev/" + disk + "1",
                   "ROOTPARENT": disk, "BOOTPARENT": disk}
            for change in ({}, {"ROOTLABEL": "/dev/foreign2"}, {"BOOTLABEL": "/dev/foreign1"},
                           {"BOOTPARENT": "other"}, {"ROOTPARENT": ""}):
                with self.subTest(disk=disk, change=change):
                    result = subprocess.run(["bash", "-c", fixtures + "\n" + " && ".join(checks)],
                                            env={**env, **change}, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, not change, result.stderr)

    def test_failed_remote_check_preserves_stdout_diagnostic(self):
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout="Kontrola neprošla: systemctl is-active --quiet systemd-networkd\n", stderr="")):
            with self.assertRaisesRegex(RuntimeError, "Kontrola neprošla.*systemd-networkd"):
                runtime.read(["ssh", "host"])

    def test_actual_shell_requires_every_service_active(self):
        for target, services in (
                ({**RPI, "spec": {**RPI["spec"], "hostName": "pi", "diskSerial": "ssd"}},
                 ["sshd", "systemd-networkd"]),
                (LIBVIRT, ["sshd", "systemd-networkd", "qemu-guest-agent"]),
                (HYPERV, ["sshd", "systemd-networkd", "hv-kvp", "hv-vss"]),
                ({"backend": "proxmox", "offline": True, "spec": {},
                  "_guestConnection": {"host": "203.0.113.19", "user": "root", "port": 2030}},
                 ["sshd", "systemd-networkd"])):
            with patch.object(runtime, "status", return_value={"state": "running", "addresses": ["10.0.0.2"]}), \
                    patch.object(runtime, "read", return_value="guest\n/nix/store/system\n/dev/sda2") as read:
                runtime.verify(target, "/nix/store/system")
            assert_services(self, read.call_args.args[0][-1], services)

    def test_rpi_hardware_and_address(self):
        with patch.object(runtime, "read", return_value=RPI_OUTPUT) as read:
            result = runtime.status(RPI)
            self.assertEqual(result["addresses"], ["192.168.50.20"])
            self.assertEqual(result["hardwareSerial"], RPI["spec"]["hardwareSerial"])
            self.assertEqual(result["root"], "/dev/sda2")
            self.assertIn("root@192.168.50.20", read.call_args.args[0])
            self.assertEqual(runtime.ssh_command(RPI, result)[-2:], ["--", "root@192.168.50.20"])

    def test_wrong_rpi_hardware_is_rejected(self):
        for field in ("aarch64", "0000000000000000", "02:00:00:00:00:20"):
            with patch.object(runtime, "read", return_value=RPI_OUTPUT.replace(field, "foreign")):
                with self.assertRaises(RuntimeError):
                    runtime.status(RPI)

    def test_rpi_cannot_connect_to_protected_address(self):
        target = {**RPI, "spec": {**RPI["spec"], "address": "192.168.79.1/24"}}
        with patch.object(runtime, "read") as read, self.assertRaises(RuntimeError):
            runtime.status(target)
        read.assert_not_called()

    def test_only_safe_lan_addresses(self):
        self.assertEqual(runtime.addresses([
            "192.168.79.1", "127.0.0.1", "169.254.1.1", "8.8.8.8", "::1", "bad",
            "192.168.77.97/24", "192.168.77.97"]), ["192.168.77.97"])

    def test_libvirt_identity_before_address(self):
        for domain in (DOMAIN.replace("expected", "foreign"),
                       DOMAIN.replace("52:54:00:79:81:01", "52:54:00:79:81:99")):
            with patch.object(runtime, "read", side_effect=["test", domain]) as read:
                with self.assertRaises(RuntimeError):
                    runtime.status(LIBVIRT)
                self.assertEqual(read.call_count, 2)

    def test_libvirt_lease_belongs_to_declared_adapter(self):
        with patch.object(runtime, "read", side_effect=["test", DOMAIN, "running", LEASES]):
            self.assertEqual(runtime.status(LIBVIRT), {
                "state": "running", "addresses": ["192.168.77.97"]})

    def test_guest_agent_supplies_address_after_dhcp_lease_expires(self):
        with patch.object(runtime, "read", side_effect=["test", DOMAIN, "running", "", LEASES]):
            self.assertEqual(runtime.status(LIBVIRT)["addresses"], ["192.168.77.97"])

    def test_verify_checks_exact_system_and_does_not_require_internet(self):
        target = {**LIBVIRT, "guestHostName": "nixberry-vm"}
        with patch.object(runtime, "status", return_value={"state": "running", "addresses": ["10.0.0.2"]}), \
                patch.object(runtime, "read", return_value="nixberry-vm\n/nix/store/system\n/dev/vda2") as read:
            result = runtime.verify(target, "/nix/store/system")
            self.assertTrue(result["verified"])
            command = read.call_args.args[0][-1]
            self.assertIn("/nix/store/system", command)
            self.assertIn("qemu-guest-agent", command)
            self.assertNotIn("curl", command)
            self.assertNotIn("NTPSynchronized", command)

    def test_absent_vm_is_not_a_connection_error(self):
        with patch.object(runtime, "read", return_value="other-vm"):
            self.assertEqual(runtime.status(LIBVIRT)["state"], "absent")

    def test_hyperv_filters_host_and_ipv6(self):
        with patch.object(runtime, "read", return_value=json.dumps(WINDOWS)):
            self.assertEqual(runtime.status(HYPERV), {
                "state": "running", "addresses": ["172.21.222.76"]})

    def test_hyperv_wrong_host_or_adapter_is_rejected(self):
        for field in ("WINDOWS-DEMO", "027981000002", "Default Switch", "test-owner"):
            with patch.object(runtime, "read", return_value=json.dumps(WINDOWS).replace(field, "foreign")):
                with self.assertRaises(RuntimeError):
                    runtime.status(HYPERV)

    def test_ssh_rejects_missing_ambiguous_or_stopped_target(self):
        for state, values in (("running", []), ("running", ["10.0.0.2", "10.0.0.3"]),
                              ("shut off", ["10.0.0.2"]), ("running", ["192.168.79.1"]),
                              ("running", ["192.168.50.10"])):
            with self.subTest(state=state, values=values), self.assertRaises(RuntimeError):
                runtime.ssh_command(HYPERV, {"state": state, "addresses": values})

    def test_hyperv_ssh_uses_jump_host(self):
        from portable import known_hosts
        self.assertEqual(runtime.ssh_command(HYPERV, {
            "state": "running", "addresses": ["172.21.222.76"]}), [
                "ssh", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-o", "UserKnownHostsFile=" + str(known_hosts(HYPERV["spec"])),
                "-o", "StrictHostKeyChecking=yes",
                "-o", "HostKeyAlias=nixberry-test-owner",
                "-J", "operator@192.168.50.10", "--", "root@172.21.222.76"])

    def test_libvirt_ssh_is_direct(self):
        command = runtime.ssh_command(LIBVIRT, {
            "state": "running", "addresses": ["192.168.77.97"]})
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertTrue(any(item.startswith("UserKnownHostsFile=") and
                            LIBVIRT["spec"]["uuid"] in item for item in command))
        self.assertEqual(runtime.ssh_command(LIBVIRT, {
            "state": "running", "addresses": ["192.168.77.97"]})[-2:], [
                "--", "root@192.168.77.97"])

    def test_hyperv_dhcp_change_keeps_strict_host_key_identity(self):
        first = runtime.ssh_command(HYPERV, {"state": "running", "addresses": ["172.21.222.76"]})
        second = runtime.ssh_command(HYPERV, {"state": "running", "addresses": ["172.21.222.77"]})
        self.assertEqual(first[:-1], second[:-1])
        self.assertIn("StrictHostKeyChecking=yes", first)
        self.assertIn("HostKeyAlias=nixberry-test-owner", first)


if __name__ == "__main__":
    unittest.main()
