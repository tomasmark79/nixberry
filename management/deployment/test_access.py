"""Trvalé SSH, oddělené možnosti správy a zachování důvěry v klíč serveru."""

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from check_inventory import evaluate, nix
from test_support import copy_test_recipe
import runtime
import updater

MAIN = runpy.run_path(str(Path(__file__).with_name("cli.py")))["main"]
ACCESS = {"host": "203.0.113.19", "port": 2030, "user": "root"}
TARGET = {"backend": "proxmox", "offline": True, "host": "offline:test", "profile": "nixberry-proxmox",
          "guestHostName": "guest", "commands": {}, "access": ACCESS,
          "spec": {"name": "guest", "vmid": 103, "vcpus": 4, "memoryMiB": 4096,
                   "diskGiB": 32, "storage": "local-lvm", "bridge": "vmbr0"}}


class AccessChecks(unittest.TestCase):
    def test_virtualbox_placement_does_not_change_server_or_image(self):
        from scaffold import new_vm
        recipe = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "recipe"
            copy_test_recipe(recipe, root)
            path, original = new_vm(root, {}, {}, "placementcheck", "virtualbox", offline=True)
            expression = '''let f = builtins.getFlake ''' + nix("path:" + str(root)) + '''; in {
              system = f.nixberrySystems.placementcheck.config.system.build.toplevel.outPath;
              image = f.nixberryImages.placementcheck.drvPath;
              target = f.nixberryDeployment.targets.placementcheck;
              manifest = builtins.fromJSON (builtins.unsafeDiscardStringContext f.packages.x86_64-linux.nixberry.manifest.text);
            }'''
            offline = evaluate(expression)
            self.assertEqual(offline["manifest"]["placementcheck"]["commands"], {})
            self.assertTrue(offline["target"]["offline"])
            hosts = json.loads(subprocess.check_output([
                "nix", "eval", "--impure", "--json", "--expr", "import " + str(root / "hosts.nix")], text=True))
            connections = {
                "windows": {"transport": "ssh", "hostPlatform": "windows", "hostName": "WINDOWS",
                            "sshHost": "192.168.50.10", "sshUser": "example", "toolPath": "VBoxManage.exe",
                            "storageDirectory": "C:\\VMs", "bridge": "Ethernet"},
                "linux": {"transport": "local", "hostPlatform": "linux", "hostName": "nixon",
                          "toolPath": "VBoxManage", "storageDirectory": "/tmp/vms", "bridge": "enp0s1"},
            }
            for name, connection in connections.items():
                hosts[name] = {"backend": "virtualbox", "connection": connection}
            (root / "hosts.local.nix").write_text(nix(hosts))
            for name in connections:
                path.write_text(original.replace('backend = "virtualbox";', 'host = "' + name + '";'))
                placed = evaluate(expression)
                self.assertFalse(placed["target"]["offline"])
                self.assertEqual(placed["system"], offline["system"])
                self.assertEqual(placed["image"], offline["image"])

    def test_capabilities_separate_image_system_and_hypervisor(self):
        self.assertEqual(runtime.capabilities(TARGET), {
            "build": True, "export": True, "system": True, "hypervisor": False})
        self.assertFalse(runtime.capabilities({**TARGET, "access": None})["system"])
        self.assertTrue(runtime.capabilities({"backend": "libvirt"})["hypervisor"])
        self.assertFalse(runtime.capabilities({"backend": "rpi"})["hypervisor"])
        self.assertTrue(runtime.capabilities({"backend": "rpi"})["system"])

    def test_offline_virtualbox_uses_direct_ssh_and_partition_identity_without_hypervisor(self):
        target = {**TARGET, "backend": "virtualbox", "profile": "nixberry-virtualbox"}
        with patch.object(runtime, "read", return_value="x86_64\nguest\n/dev/sda2") as read:
            self.assertEqual(runtime.status(target)["state"], "running")
        self.assertNotIn("-J", read.call_args.args[0])
        self.assertEqual(runtime.direct_connection(target)["port"], 2030)
        checks = "\n".join(runtime.installation_checks(target))
        self.assertIn("by-partlabel", checks)
        self.assertNotIn("product_uuid", checks)
        self.assertFalse(runtime.capabilities(target)["hypervisor"])

    def test_invalid_access_is_rejected_without_connections(self):
        for access in ({}, {**ACCESS, "port": True}, {**ACCESS, "port": "2030"},
                       {**ACCESS, "user": "other"}, {**ACCESS, "knownHostsFile": "relative"},
                       {**ACCESS, "hostKeyAlias": "host\nStrictHostKeyChecking=no"},
                       {**ACCESS, "strictHostKeyChecking": False}):
            with self.subTest(access=access), patch.object(runtime, "read") as read, self.assertRaises(ValueError):
                runtime.status({**TARGET, "access": access})
            read.assert_not_called()

    def test_ssh_override_keeps_declared_host_key_policy(self):
        target = {**TARGET, "access": {**ACCESS, "knownHostsFile": "/tmp/known hosts", "hostKeyAlias": "guest"},
                  "_guestConnection": runtime.guest_connection("root@new.example:2222")}
        direct = runtime.direct_connection(target)
        self.assertEqual(direct["hostKeyAlias"], "guest")
        self.assertEqual(direct["knownHostsFile"], "/tmp/known hosts")
        command = runtime.ssh_command(target, {"state": "running"})
        output = subprocess.check_output([command[0], "-F", "/dev/null", "-G", *command[1:]],
                                         text=True, stderr=subprocess.DEVNULL)
        self.assertIn("hostname new.example", output)
        self.assertIn("port 2222", output)
        self.assertIn("hostkeyalias guest", output)
        self.assertIn("/tmp/known hosts", output)
        self.assertIn("stricthostkeychecking true", output)

    def test_cli_resolves_declared_access_and_never_writes_without_apply(self):
        update = Mock()
        with patch("pathlib.Path.read_text", return_value=json.dumps({"test": TARGET})), \
                patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT": "/registry"}), \
                patch.dict(MAIN.__globals__, {"update": update}), patch("subprocess.run") as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                MAIN(["list"])
                MAIN(["show", "test", "--json"])
                self.assertEqual(MAIN(["update", "test"]), 0)
            self.assertIn("configured", output.getvalue())
            self.assertIn("unmanaged", output.getvalue())
            self.assertEqual(update.call_args.kwargs, {"apply": False})
            self.assertEqual(runtime.direct_connection(update.call_args.args[2])["port"], 2030)
            with contextlib.redirect_stdout(io.StringIO()):
                MAIN(["update", "test", "--ssh", "root@temporary.example:2222"])
            self.assertEqual(runtime.direct_connection(update.call_args.args[2])["host"], "temporary.example")
            for action in ("plan", "create", "reproduce", "reset"):
                with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    MAIN([action, "test"])
            run.assert_not_called()

    def test_declared_status_checks_real_hostname_and_ssh_identity(self):
        with patch.object(runtime, "read", return_value="x86_64\nguest\n/dev/sda2") as read:
            snapshot = runtime.status(TARGET)
        self.assertEqual(snapshot["state"], "running")
        self.assertIn("StrictHostKeyChecking=yes", read.call_args.args[0])
        with patch.object(runtime, "read", return_value="x86_64\nforeign\n/dev/sda2"), self.assertRaises(RuntimeError):
            runtime.status(TARGET)

    def test_declared_update_plan_keeps_simple_commands_and_does_not_connect(self):
        recipe = str(Path(__file__).resolve().parents[2])
        with patch.object(updater, "status") as status, patch.object(updater, "build") as build, \
                contextlib.redirect_stdout(io.StringIO()) as stdout, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            updater.update(recipe, "test", TARGET)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("203.0.113.19:2030", output.getvalue())
        self.assertIn("nixberry update test --apply", output.getvalue())
        status.assert_not_called()
        build.assert_not_called()

    def test_access_changes_do_not_change_the_system_or_disk_image(self):
        from scaffold import new_vm
        recipe = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "recipe"
            copy_test_recipe(recipe, root)
            path, text = new_vm(str(root), {}, {}, "accesscheck", "proxmox", None, write=False, offline=True)
            original = text.replace("{\n", '{\n  access = { host = "203.0.113.19"; port = 2030; };\n', 1)
            path.write_text(original)
            expression = '''let f = builtins.getFlake ''' + nix("path:" + str(root)) + '''; in {
              system = f.nixberrySystems.accesscheck.config.system.build.toplevel.outPath;
              image = f.nixberryImages.accesscheck.drvPath;
              target = f.nixberryDeployment.targets.accesscheck;
              manifest = builtins.fromJSON (builtins.unsafeDiscardStringContext f.packages.x86_64-linux.nixberry.manifest.text);
            }'''
            before = evaluate(expression)
            path.write_text(original.replace('host = "203.0.113.19"', 'host = "new.example"').replace("port = 2030", "port = 2222"))
            after = evaluate(expression)
            self.assertEqual(before["system"], after["system"])
            self.assertEqual(before["image"], after["image"])
            self.assertEqual(after["target"]["access"]["port"], 2222)
            target = after["manifest"]["accesscheck"]
            self.assertTrue(target["capabilities"]["system"])
            self.assertFalse(target["capabilities"]["hypervisor"])
            self.assertIn("update", target["operations"])
            self.assertNotIn("reproduce", target["operations"])
            self.assertEqual(target["commands"], {})
            path.write_text(original.replace('  access = {', '  access = null;\n  unusedAccess = {'))
            unset = evaluate(expression)
            self.assertEqual(unset["system"], before["system"])
            self.assertEqual(unset["manifest"]["accesscheck"]["operations"], ["show", "export"])


if __name__ == "__main__":
    unittest.main()
