"""Kontrola služeb po startu Hyper-V musí odmítnout každou neaktivní jednotku."""

from pathlib import Path
import sys
import os
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parents[1] / "deployment"))
from manage import installed_check, Manager
from service_test import assert_boot_partitions, assert_services


class ServiceChecks(unittest.TestCase):
    def test_valid_declaration_without_uuid_uses_owner_id_consistently(self):
        manager = Manager.__new__(Manager)
        manager.spec = {"ownerId": "nixberry-hyperv-test"}
        self.assertEqual(manager.key_identity(), "nixberry-hyperv-test")

    def test_installed_guest_uses_stable_alias_but_installer_does_not(self):
        manager = Manager.__new__(Manager)
        manager.spec = {"uuid": "test"}
        manager.state = Path("/temporary")
        manager.host = "admin@host"
        with patch("manage.subprocess.run") as run:
            manager.guest("10.0.0.2", "true", installed=True)
            self.assertIn("HostKeyAlias=nixberry-test", run.call_args.args[0])
            manager.guest("10.0.0.3", "true", installed=True)
            self.assertIn("HostKeyAlias=nixberry-test", run.call_args.args[0])
            manager.guest("10.0.0.2", "true")
            self.assertFalse(any(arg.startswith("HostKeyAlias=") for arg in run.call_args.args[0]))

    def test_only_verified_system_key_is_retained(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"XDG_STATE_HOME": root}):
            manager = Manager.__new__(Manager)
            manager.spec = {"ownerId": "nixberry-hyperv-test", "uuid": "test"}
            manager.state = Path(root) / "temporary"
            manager.state.mkdir()
            (manager.state / "installer-hosts").write_text("installer-key\n")
            (manager.state / "installed-hosts").write_text("installed-key\n")
            manager.remember_host_key()
            destination = Path(root) / "nixberry/portable/test/known_hosts"
            self.assertEqual(destination.read_text(), "installed-key\n")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_installed_luks_check_uses_nested_manifest_backend(self):
        spec = {"guestHostName": "guest", "security": {"mode": "luks"},
                "securityTarget": {"backend": "hyperv", "security": {"mode": "luks"}}}
        self.assertIn("cryptsetup status nixberry-root", installed_check({"biosGuid": "uuid"}, spec))

    def test_installed_ssh_uses_existing_pin_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"XDG_STATE_HOME": root}):
            manager = Manager.__new__(Manager)
            manager.spec = {"uuid": "test"}
            manager.state = Path(root) / "temporary"
            manager.host = "admin@host"
            pin = Path(root) / "nixberry/portable/test/known_hosts"
            pin.parent.mkdir(parents=True)
            pin.write_text("nixberry-test ssh-ed25519 old-key\n")
            with patch("manage.subprocess.run") as run:
                manager.guest("10.0.0.2", "true", installed=True)
            self.assertIn("UserKnownHostsFile=" + str(pin), run.call_args.args[0])
            self.assertIn("StrictHostKeyChecking=yes", run.call_args.args[0])
            manager.remember_host_key()
            self.assertEqual(pin.read_text(), "nixberry-test ssh-ed25519 old-key\n")

    def test_actual_installed_shell_requires_every_service_active(self):
        script = installed_check({"biosGuid": "uuid"}, {"guestHostName": "guest"})
        assert_services(self, script, ["sshd", "systemd-networkd", "hv-kvp", "hv-vss"])

    def test_actual_installed_shell_rejects_foreign_boot_or_mounted_iso(self):
        script = installed_check({"biosGuid": "uuid"}, {"guestHostName": "guest"})
        assert_boot_partitions(self, script, "sda")

    def test_installed_check_verifies_declared_root_filesystem_with_nofsroot(self):
        for filesystem in ("ext4", "btrfs"):
            script = installed_check({"biosGuid": "uuid"}, {"guestHostName": "guest", "rootFilesystem": filesystem,
                                                            "systemPath": "/nix/store/expected-system"})
            self.assertIn('test "$(readlink -f /run/current-system)" = /nix/store/expected-system', script)
            self.assertIn('test "$(findmnt -n --nofsroot -o SOURCE /)" = /dev/sda2', script)
            self.assertIn(f'test "$(findmnt -n -o FSTYPE /)" = {filesystem}', script)
            self.assertIn('test "$(findmnt -n -o FSTYPE /boot)" = vfat', script)
            self.assertLess(script.index("systemctl is-active"), script.index("\nhostname\n"))
        with self.assertRaisesRegex(RuntimeError, "ext4 or btrfs"):
            installed_check({"biosGuid": "uuid"}, {"guestHostName": "guest", "rootFilesystem": "xfs"})
