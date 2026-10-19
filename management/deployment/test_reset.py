"""Regresní ochrany resetu; transporty a odstranění jsou nahrazené, bez živých VM."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import host_agent
import reset


class ResetChecks(unittest.TestCase):
    def test_cli_refuses_permission_before_transport(self):
        with patch.dict(reset.os.environ, {"NIXBERRY_RESET_TARGET": "/unused"}), \
                patch.object(reset.Path, "read_text", return_value='{"allowReset": false}'), \
                patch.object(reset, "rpc") as rpc, patch.object(reset, "hyperv") as hyperv:
            with self.assertRaises(RuntimeError):
                reset.main([])
            rpc.assert_not_called()
            hyperv.assert_not_called()

    def test_rpc_rejects_missing_or_truthy_permission_before_host_access(self):
        for allowed in (None, False, 1, "true"):
            with self.subTest(allowed=allowed), patch.object(host_agent, "host_check") as host:
                with self.assertRaises(ValueError):
                    host_agent.dispatch({"spec": {}, "backend": "proxmox", "action": "reset",
                                         "apply": True, "allowReset": allowed})
                host.assert_not_called()

    def test_rpc_rejects_ambiguous_apply_before_host_access(self):
        with patch.object(host_agent, "host_check") as host:
            with self.assertRaises(ValueError):
                host_agent.dispatch({"spec": {}, "backend": "vmware", "action": "reset",
                                     "apply": "false", "allowReset": True})
            host.assert_not_called()

    def test_read_only_reset_never_stops_or_deletes(self):
        plan = {"state": "running", "uuid": "id", "directory": "/owned"}
        with patch.object(host_agent, "reset_checks", return_value=plan), \
                patch.object(host_agent, "run") as run, patch.object(host_agent.shutil, "rmtree") as remove:
            result = host_agent.reset_vm({}, "virtualbox", Path("/owned"), apply=False)
        self.assertTrue(result["readOnly"])
        run.assert_not_called()
        remove.assert_not_called()

    def test_changed_identity_after_shutdown_prevents_delete(self):
        spec = {"toolPath": "VBoxManage", "uuid": "id"}
        first = {"state": "running", "uuid": "id", "directory": "/owned"}
        second = {"state": "shut off", "uuid": "different", "directory": "/owned"}
        with patch.object(host_agent, "reset_checks", side_effect=[first, second]), \
                patch.object(host_agent, "probe", return_value={"state": "shut off"}), \
                patch.object(host_agent, "run") as run, patch.object(host_agent.shutil, "rmtree") as remove:
            with self.assertRaises(RuntimeError):
                host_agent.reset_vm(spec, "virtualbox", Path("/owned"), apply=True)
        self.assertEqual(run.call_args_list[0].args[0], ["VBoxManage", "controlvm", "id", "acpipowerbutton"])
        self.assertEqual(run.call_count, 1)
        remove.assert_not_called()

    def test_symlink_in_owned_tree_is_rejected_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside").symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                host_agent.reset_tree(root)

    def test_permission_switch_does_not_change_persisted_vm_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = {"uuid": "stable", "name": "vm"}
            (root / "nixberry.json").write_text(json.dumps({"spec": spec}))
            self.assertEqual(host_agent.owned(root, spec)["spec"], spec)
            # Oprávnění patří k požadavku/instanci, nepřidává se do spec markeru.
            for permission in (False, True):
                target = {"allowReset": permission, "spec": spec}
                self.assertEqual(host_agent.owned(root, target["spec"])["spec"], spec)
