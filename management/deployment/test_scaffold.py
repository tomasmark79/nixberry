"""Další deklarace zachová roli, stabilní identitu a nepřepíše existující recept."""

import json
import runpy
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scaffold import new_libvirt, new_vm

HOSTS = {"local-libvirt": {"backend": "libvirt", "connection": {"uri": "qemu:///system"}}}


class ScaffoldChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "instances").mkdir(parents=True)
        (self.root / "flake.nix").touch()
        (self.root / "instances/default.nix").touch()
        (self.root / "configurations").mkdir()
        (self.root / "configurations/default.nix").write_text("{}")

    def test_every_backend_prepares_offline_without_hosts_and_can_attach(self):
        recipe = Path(__file__).parents[2]
        shutil.copytree(recipe / "management/deployment", self.root / "management/deployment", dirs_exist_ok=True)
        shutil.copyfile(recipe / "instances/default.nix", self.root / "instances/default.nix")
        (self.root / "hosts.nix").write_text("{}")
        (self.root / "management/rpi/targets").mkdir(parents=True)
        (self.root / "management/rpi/targets/default.nix").write_text("{}")
        def evaluate(name):
            return json.loads(subprocess.check_output([
                "nix", "eval", "--impure", "--json", "--expr",
                "(import " + str(self.root / "management/deployment") + ").targets." + name], text=True))
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            with self.subTest(backend=backend):
                path, text = new_vm(self.root, {"available": {"backend": backend}}, {}, backend, backend, offline=True)
                target = evaluate(backend)
                self.assertTrue(target["offline"])
                self.assertEqual(target["backend"], backend)
                self.assertEqual(target["capabilities"], {"build": True, "export": True, "system": False, "hypervisor": False})
                self.assertEqual(target["network"]["mode"], "dhcp")
                path.write_text(text.replace("  spec = {", '  access = { host = "server.example"; };\n  spec = {'))
                self.assertTrue(evaluate(backend)["capabilities"]["system"])
                (self.root / "hosts.nix").write_text('{ online = { backend = "' + backend + '"; connection.transport = "local"; }; }')
                path.write_text(text.replace('backend = "' + backend + '";', 'host = "online";'))
                attached = evaluate(backend)
                self.assertFalse(attached["offline"])
                self.assertEqual(attached["spec"]["uuid"], target["spec"]["uuid"])
                # Ostatní deklarace musí zůstat vyhodnotitelné i po změně hostitele.
                path.write_text(text)
                with self.assertRaises(RuntimeError):
                    new_vm(self.root, {}, {}, "invalid", backend, "host", offline=True)

    def test_hyperv_declaration_passes_backend_policy(self):
        hosts = {"windows": {"backend": "hyperv", "connection": {
            "storageDirectory": "C:\\VMs", "switchId": "12345678-1234-4321-8123-123456789abc"}}}
        path, text = new_vm(self.root, hosts, {}, "example", "hyperv")
        declaration = json.loads(subprocess.check_output(
            ["nix", "eval", "--impure", "--json", "--expr", "import " + str(path)], text=True))
        validate = runpy.run_path(str(Path(__file__).parents[1] / "hyperv/policy.py"))["validate"]
        validate({**hosts["windows"]["connection"], **declaration["spec"]})
        self.assertEqual(declaration["host"], "windows")
        self.assertEqual(declaration["profile"], "nixberry-hyperv")
        self.assertEqual(declaration["spec"]["name"], "nixberry-example")
        with self.assertRaises(RuntimeError):
            new_vm(self.root, hosts, {}, "example", "hyperv")
        self.assertEqual(path.read_text(), text)

    def test_offline_proxmox_defaults_evaluate_and_can_attach_to_host(self):
        recipe = Path(__file__).parents[2]
        shutil.copytree(recipe / "management/deployment", self.root / "management/deployment", dirs_exist_ok=True)
        shutil.copyfile(recipe / "instances/default.nix", self.root / "instances/default.nix")
        (self.root / "hosts.nix").write_text("{}")
        (self.root / "management/rpi/targets").mkdir(parents=True)
        (self.root / "management/rpi/targets/default.nix").write_text("{}")
        registry = {"other": {"backend": "proxmox", "host": "other-pve", "spec": {"vmid": 1000}}}
        # Výslovná offline volba nesmí vybrat ani dostupného hostitele.
        path, original = new_vm(self.root, {"pve": {"backend": "proxmox"}}, registry, "offline", "proxmox", offline=True)

        def evaluate():
            return json.loads(subprocess.check_output([
                "nix", "eval", "--impure", "--json", "--expr",
                "(import " + str(self.root / "management/deployment") + ").targets.offline"], text=True))

        target = evaluate()
        self.assertTrue(target["offline"])
        self.assertEqual(target["profile"], "nixberry-proxmox")
        self.assertEqual(target["spec"]["transport"], "offline")
        self.assertEqual(target["spec"]["vmid"], 1001)
        self.assertEqual(target["spec"]["storage"], "local-lvm")
        self.assertEqual(target["spec"]["bridge"], "vmbr0")
        self.assertEqual([target["spec"][k] for k in ("vcpus", "memoryMiB", "diskGiB")], [4, 4096, 32])
        path.write_text(original.replace('storage = "local-lvm"', 'storage = "ssd"').replace("vcpus = 4", "vcpus = 8"))
        edited = evaluate()
        self.assertEqual(edited["spec"]["storage"], "ssd")
        self.assertEqual(edited["spec"]["vcpus"], 8)
        with self.assertRaises(RuntimeError):
            new_vm(self.root, {}, {}, "offline", "proxmox")
        connection = {"transport": "ssh", "storage": "production", "bridge": "vmbr1"}
        (self.root / "hosts.nix").write_text(
            '{ pve = { backend = "proxmox"; connection = { transport = "ssh"; storage = "production"; bridge = "vmbr1"; }; }; }')
        path.write_text(path.read_text().replace('backend = "proxmox";', 'host = "pve";'))
        attached = evaluate()
        self.assertFalse(attached["offline"])
        for key, value in connection.items():
            self.assertEqual(attached["spec"][key], value)
        self.assertEqual(attached["spec"]["uuid"], target["spec"]["uuid"])
        self.assertEqual(attached["guestHostName"], target["guestHostName"])

    def test_offline_proxmox_dry_run_needs_no_hosts(self):
        path, text = new_vm(self.root, {}, {}, "offline", "proxmox", write=False, offline=True)
        self.assertFalse(path.exists())
        self.assertIn('backend = "proxmox"', text)
        with self.assertRaises(RuntimeError):
            new_vm(self.root, {}, {}, "offline", "proxmox", "missing")

    def test_proxmox_defaults_to_single_online_host_and_requires_selection(self):
        path, text = new_vm(self.root, {"pve": {"backend": "proxmox"}}, {}, "online", "proxmox", write=False)
        self.assertIn('host = "pve";', text)
        self.assertNotIn('backend = "proxmox";', text)
        self.assertFalse(path.exists())
        for hosts in ({}, {"pve": {"backend": "proxmox"}, "pve2": {"backend": "proxmox"}}):
            with self.subTest(hosts=hosts), self.assertRaises(RuntimeError) as error:
                new_vm(self.root, hosts, {}, "online", "proxmox", write=False)
            self.assertIn("--host", str(error.exception))
            self.assertIn("--offline", str(error.exception))

    def test_virtualbox_offline_evaluates_without_host_and_attaches_without_changing_system(self):
        recipe = Path(__file__).parents[2]
        shutil.copytree(recipe / "management/deployment", self.root / "management/deployment", dirs_exist_ok=True)
        shutil.copyfile(recipe / "instances/default.nix", self.root / "instances/default.nix")
        (self.root / "hosts.nix").write_text("{}")
        (self.root / "management/rpi/targets").mkdir(parents=True)
        (self.root / "management/rpi/targets/default.nix").write_text("{}")
        path, original = new_vm(self.root, {}, {}, "offlinevbox", "virtualbox", offline=True)
        def evaluate():
            return json.loads(subprocess.check_output([
                "nix", "eval", "--impure", "--json", "--expr",
                "(import " + str(self.root / "management/deployment") + ").targets.offlinevbox"], text=True))
        offline = evaluate()
        self.assertTrue(offline["offline"])
        self.assertEqual(offline["spec"]["transport"], "offline")
        self.assertEqual(offline["network"]["mode"], "dhcp")
        self.assertEqual(offline["capabilities"], {
            "build": True, "export": True, "system": False, "hypervisor": False})
        path.write_text(original.replace('backend = "virtualbox";',
                                        'backend = "virtualbox"; access = { host = "192.168.79.20"; };'))
        self.assertTrue(evaluate()["capabilities"]["system"])
        (self.root / "hosts.nix").write_text('{ local = { backend = "virtualbox"; connection.transport = "local"; }; }')
        path.write_text(original.replace('backend = "virtualbox";', 'host = "local";'))
        attached = evaluate()
        self.assertFalse(attached["offline"])
        self.assertEqual(attached["spec"]["uuid"], offline["spec"]["uuid"])
        self.assertEqual(attached["network"], offline["network"])
        for backend, host in (("libvirt", "host"), ("hyperv", "host"), ("vmware", "host"), ("virtualbox", "local")):
            with self.subTest(backend=backend, host=host), self.assertRaises(RuntimeError):
                new_vm(self.root, {}, {}, "other", backend, host, offline=True)

    def test_every_proxmox_declaration_has_editable_network(self):
        for name, host in (("offline", None), ("connected", "pve")):
            with self.subTest(host=host):
                path, text = new_vm(self.root, {"pve": {"backend": "proxmox"}}, {}, name, "proxmox", host, offline=host is None)
                declaration = json.loads(subprocess.check_output([
                    "nix", "eval", "--impure", "--json", "--expr", "import " + str(path)], text=True))
                self.assertEqual(declaration["network"], {
                    "mode": "dhcp", "address": "", "gateway": "", "dns": []})
                self.assertIn('mode = "static"', text)
                self.assertIn('10.0.1.19/24', text)

    def test_hyperv_host_selection_dry_run_and_identity_guards(self):
        host = {"backend": "hyperv"}
        hosts = {"one": host, "two": host, **HOSTS}
        path, _ = new_vm(self.root, hosts, {}, "example", "hyperv", "two", write=False)
        self.assertFalse(path.exists())
        for candidates, selected in (({}, None), (hosts, None), (hosts, "local-libvirt")):
            with self.subTest(host=selected), self.assertRaises(RuntimeError):
                new_vm(self.root, candidates, {}, "example", "hyperv", selected)
        for name in ("../bad", "Bad", "bad-", "a" * 41):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                new_vm(self.root, hosts, {}, name, "hyperv", "one")
        with self.assertRaises(RuntimeError):
            new_vm(self.root, hosts, {"example": {}}, "example", "hyperv", "one")
        with patch("scaffold.uuid.uuid4") as identity:
            identity.return_value.hex = "0" * 26 + "abcdef"
            with self.assertRaisesRegex(RuntimeError, "MAC conflicts"):
                new_vm(self.root, hosts, {"other": {"spec": {"mac": "02:79:81:AB:CD:EF"}}},
                       "example", "hyperv", "one")

    def test_new_declaration_is_stable_and_keeps_role(self):
        path, text = new_libvirt(self.root, HOSTS, {}, "lab", "local-libvirt")
        self.assertEqual(path, self.root / "instances/lab.nix")
        self.assertEqual(path.read_text(), text)
        self.assertIn('profile = "nixberry-vm"', text)
        self.assertIn("allowReset = false", text)
        self.assertIn('name = "nixberry-lab"', text)
        with self.assertRaises(RuntimeError):
            new_libvirt(self.root, HOSTS, {}, "lab", "local-libvirt")
        self.assertEqual(path.read_text(), text)

    def test_dry_run_writes_nothing(self):
        path, _ = new_libvirt(self.root, HOSTS, {}, "lab", "local-libvirt", write=False)
        self.assertFalse(path.exists())

    def test_automatic_host_selection_does_not_depend_on_its_name(self):
        hosts = {"another-host": HOSTS["local-libvirt"]}
        _, text = new_libvirt(self.root, hosts, {}, "lab", write=False)
        self.assertIn('host = "another-host"', text)
        for candidates in ({}, {"one": hosts["another-host"], "two": hosts["another-host"]}):
            with self.subTest(candidates=candidates), self.assertRaises(RuntimeError):
                new_libvirt(self.root, candidates, {}, "lab", write=False)

    def test_invalid_or_existing_target_is_rejected(self):
        for name in ("../lab", "lab\";", "Lab", "-option", "lab-", "a" * 41):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                new_libvirt(self.root, HOSTS, {}, name, "local-libvirt")
        with self.assertRaises(RuntimeError):
            new_libvirt(self.root, HOSTS, {"lab": {}}, "lab", "local-libvirt")

    def test_remote_or_unknown_hypervisor_is_not_assumed_supported(self):
        for hosts in ({}, {"local-libvirt": {"backend": "hyperv"}},
                      {"local-libvirt": {"backend": "libvirt", "connection": {"uri": "qemu+ssh://other/system"}}}):
            with self.assertRaises(RuntimeError):
                new_libvirt(self.root, hosts, {}, "lab", "local-libvirt")


if __name__ == "__main__":
    unittest.main()
