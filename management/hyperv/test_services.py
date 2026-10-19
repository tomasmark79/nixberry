"""Kontrola služeb po startu Hyper-V musí odmítnout každou neaktivní jednotku."""

from pathlib import Path
import sys
import os
import tempfile
import unittest
from unittest.mock import patch

from manage import installed_check, Manager
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service_test import assert_services


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

    def test_actual_installed_shell_requires_every_service_active(self):
        script = installed_check({"biosGuid": "uuid"}, {"guestHostName": "guest"})
        assert_services(self, script, ["sshd", "systemd-networkd", "hv-kvp", "hv-vss"])
