"""Výběr konfigurace nesmí záviset na backendu ani povolit neznámý recept."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scaffold import configuration_name, new_vm


class ConfigurationChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "configurations/web").mkdir(parents=True)
        (self.root / "configurations/default.nix").write_text("{}")
        (self.root / "configurations/web/default.nix").write_text("{}")
        (self.root / "instances").mkdir()
        (self.root / "instances/default.nix").write_text("{}")
        (self.root / "flake.nix").write_text("{}")
        (self.root / "management/deployment").mkdir(parents=True)
        shutil.copyfile(Path(__file__).with_name("configurations.nix"),
                        self.root / "management/deployment/configurations.nix")

    def nix(self, expression, success=True):
        result = subprocess.run(["nix", "eval", "--impure", "--json", "--expr",
                                 "let c = import " + str(self.root / "management/deployment/configurations.nix")
                                 + "; in " + expression], text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result.stderr

    def test_nix_defaults_and_directory_modules_match_scaffold(self):
        self.assertEqual(self.nix("builtins.attrNames c.modules"), ["default", "web"])
        self.assertEqual(self.nix("c.select {}"), "default")
        for name in ("default", "web"):
            self.assertEqual(configuration_name(self.root, name), self.nix('c.select { configuration = "' + name + '"; }'))

    def test_unknown_or_invalid_configuration_is_rejected_before_write(self):
        for name in ("typo", "../web", "", "${builtins.abort}"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Available configurations: default, web"):
                new_vm(self.root, {}, {}, "demo", "libvirt", offline=True, configuration=name)
        self.assertFalse((self.root / "instances/demo.nix").exists())
        for value in ('"typo"', '"../web"', 'null', '[ "web" ]'):
            self.assertIn("Available configurations: default, web", self.nix("c.select { configuration = " + value + "; }", success=False))

    def test_backend_independent_selection_including_local_libvirt(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            for offline in (True, False):
                hosts = {"local": {"backend": backend, "connection": {"uri": "qemu:///system"}}}
                _, text = new_vm(self.root, hosts, {}, "demo", backend, write=False,
                                 offline=offline, configuration="web")
                self.assertIn('configuration = "web";', text)
                self.assertIn('allowReset = false;', text)
                self.assertIn('network = {\n    mode = "dhcp";', text)
                self.assertIn('address = "";', text)
                self.assertIn('gateway = "";', text)
                self.assertIn('dns = [ ];', text)

    def test_duplicate_definitions_fail_in_nix_and_scaffold(self):
        (self.root / "configurations/web.nix").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "file and a directory"):
            configuration_name(self.root, "web")
        self.assertIn("file and a directory", self.nix("c.select {}", success=False))

    def test_directory_links_are_configurations_but_file_links_are_not(self):
        (self.root / "configurations/link.nix").symlink_to("default.nix")
        (self.root / "configurations/empty").mkdir()
        (self.root / "configurations/linked").symlink_to("web", target_is_directory=True)
        self.assertEqual(self.nix("builtins.attrNames c.modules"), ["default", "linked", "web"])
        self.assertEqual(configuration_name(self.root, "linked"), "linked")
        for name in ("link", "empty"):
            with self.assertRaises(RuntimeError):
                configuration_name(self.root, name)

    def test_vm_and_rpi_registry_keep_selection_out_of_hardware_spec(self):
        shutil.copyfile(Path(__file__).with_name("default.nix"), self.root / "management/deployment/default.nix")
        shutil.copyfile(Path(__file__).with_name("security.nix"), self.root / "management/deployment/security.nix")
        shutil.copyfile(Path(__file__).with_name("vm-network.nix"), self.root / "management/deployment/vm-network.nix")
        (self.root / "hosts.nix").write_text("{}")
        (self.root / "instances/default.nix").write_text(
            '{ demo = { backend = "libvirt"; configuration = "web"; spec.name = "demo"; }; }')
        (self.root / "management/rpi").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / "rpi/security.nix", self.root / "management/rpi/security.nix")
        targets = self.root / "management/rpi/targets"
        targets.mkdir(parents=True)
        (targets / "default.nix").write_text('{ pi = { configuration = "web"; hostName = "pi"; }; }')
        def registry():
            return json.loads(subprocess.check_output([
                "nix", "eval", "--impure", "--json", "--expr",
                "(import " + str(self.root / "management/deployment") + ").targets"], text=True))
        for target in registry().values():
            self.assertEqual(target["configuration"], "web")
            self.assertNotIn("configuration", target["spec"])
        (targets / "default.nix").write_text('{ pi = { hostName = "pi"; }; }')
        self.assertEqual(registry()["pi"]["configuration"], "default")
