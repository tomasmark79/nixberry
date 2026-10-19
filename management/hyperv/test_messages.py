"""Vytvoření VM předává JSON jako hodnotu hlášky, nikoli jako formátovací řetězec."""

import contextlib
import io
import json
import unittest
from unittest.mock import Mock

from manage import Manager


class HyperVOutputChecks(unittest.TestCase):
    def test_created_vm_json_is_printed_without_corrupting_message_or_plan(self):
        manager = object.__new__(Manager)
        manager.spec = {"deploymentMethod": "iso"}
        manager.remote = Mock(side_effect=[{"readOnly": True}, {"cached": True},
                                          {"id": "vm-id", "state": "Running"}])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            manager.create(True)
        self.assertEqual(json.loads(stdout.getvalue()), {"readOnly": True})
        self.assertIn('[nixberry] VM created and booted from ISO: {"id": "vm-id", "state": "Running"}', stderr.getvalue())

    def test_image_install_starts_and_verifies_without_guest_installer(self):
        manager = object.__new__(Manager)
        manager.spec = {"deploymentMethod": "image", "guestHostName": "nixberry-test"}
        snapshot = {"biosGuid": "12345678-1234-4321-8123-123456789abc"}
        manager.remote = Mock()
        manager.wait_guest = Mock(return_value=(snapshot, "10.0.0.2"))
        manager.guest = Mock(return_value=Mock(returncode=0))
        manager.remember_host_key = Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            manager.install(False)
            manager.remember_host_key.assert_not_called()
            manager.wait_guest.assert_not_called()
            manager.guest.assert_not_called()
            manager.install(True)
        manager.remember_host_key.assert_called_once_with()
        self.assertEqual([c.args[0] for c in manager.remote.call_args_list], ["status", "status", "start-disk"])
        command = manager.guest.call_args.args[1]
        self.assertNotIn("nixos-install", command)
        self.assertIn("findmnt", command)


if __name__ == "__main__":
    unittest.main()
