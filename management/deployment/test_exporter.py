"""Offline balíček uchová recept, systém a kontrolní součty a nepřepíše cizí data."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from exporter import export


class ExportChecks(unittest.TestCase):
    def test_named_configuration_is_in_manifest_and_frozen_recipe(self):
        self.target["configuration"] = "web"
        (self.recipe / "configurations").mkdir()
        (self.recipe / "configurations/web.nix").write_text('{ services.nginx.enable = true; }')
        shutil.copytree(self.recipe / "configurations", self.snapshot / "configurations")
        destination = self.root / "configured-bundle"
        with patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(self.image))), \
                patch("exporter.subprocess.check_output", side_effect=self.nix_output):
            manifest = export(self.recipe, "lab", self.target, destination)
        self.assertEqual(manifest["configuration"], "web")
        self.assertEqual(json.loads((destination / "manifest.json").read_text())["configuration"], "web")
        self.assertEqual((destination / "recipe/configurations/web.nix").read_text(),
                         (self.recipe / "configurations/web.nix").read_text())
        self.assertIn("recipe/configurations/web.nix", (destination / "SHA256SUMS").read_text())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recipe = self.root / "recipe"
        self.recipe.mkdir()
        (self.recipe / "flake.lock").write_text('{"pinned":true}')
        (self.recipe / "README.md").write_text("Návod k offline startu")
        self.image = self.root / "store-image"
        self.image.mkdir()
        (self.image / "system.qcow2").write_bytes(b"prepared-disk")
        self.target = {"backend": "libvirt", "profile": "nixberry-vm", "spec": {"uuid": "test"}}
        self.snapshot = self.root / "snapshot"
        shutil.copytree(self.recipe, self.snapshot)

    def nix_output(self, command, **kwargs):
        if command[1:3] == ["flake", "metadata"]:
            return json.dumps({"path": str(self.snapshot)})
        if "#nixberryDeployment.targets." in command[-1]:
            return json.dumps(self.target)
        return "/nix/store/expected-system"

    def test_unwritable_destination_fails_before_nix_with_actionable_path(self):
        destination = self.root / "unwritable" / "bundle"
        for operation in ("exporter.Path.mkdir", "exporter.tempfile.TemporaryDirectory"):
            with self.subTest(operation=operation), patch(operation, side_effect=PermissionError()), \
                    patch("exporter.subprocess.check_output") as nix:
                with self.assertRaises(RuntimeError) as error:
                    export(self.recipe, "lab", self.target, destination)
                self.assertIn(str(destination), str(error.exception))
                self.assertIn("--output ~/lab-offline", str(error.exception))
                nix.assert_not_called()


    def test_bundle_has_complete_recipe_and_valid_hashes(self):
        destination = self.root / "bundle"
        with patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(self.image))), \
                patch("exporter.subprocess.check_output", side_effect=self.nix_output) as expected:
            result = export(self.recipe, "lab", self.target, destination)
        self.assertIn("#nixberrySystems.lab.", expected.call_args.args[0][-1])
        self.assertEqual(result["systemPath"], "/nix/store/expected-system")
        self.assertEqual((destination / "recipe/flake.lock").read_bytes(), (self.recipe / "flake.lock").read_bytes())
        self.assertEqual(json.loads((destination / "manifest.json").read_text())["artifact"], "nixberry-lab.qcow2")
        for line in (destination / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)

    def test_proxmox_handoff_includes_static_network_and_existing_vm_instructions(self):
        self.target = {"backend": "proxmox", "profile": "nixberry-proxmox", "guestHostName": "kozlonix",
                       "network": {"mode": "static", "address": "10.0.1.19/24",
                                   "gateway": "10.0.1.1", "dns": ["10.0.1.1"]},
                       "spec": {"vmid": 103, "vcpus": 4, "memoryMiB": 4096, "diskGiB": 32}}
        destination = self.root / "bundle"
        with patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(self.image))), \
                patch("exporter.subprocess.check_output", side_effect=self.nix_output):
            manifest = export(self.recipe, "kozlonix", self.target, destination)
        text = (destination / "PROXMOX.txt").read_text()
        for value in ("VM103", "10.0.1.19/24", "10.0.1.1", "virtio0", "UEFI/OVMF", "MAC and UUID"):
            self.assertIn(value, text)
        self.assertEqual(manifest["network"], self.target["network"])
        self.assertIn("PROXMOX.txt", (destination / "SHA256SUMS").read_text())

    def test_virtualbox_handoff_has_boot_network_and_ssh_instructions_and_hashes(self):
        (self.image / "system.qcow2").rename(self.image / "system.vdi")
        self.target = {"backend": "virtualbox", "profile": "nixberry-virtualbox", "guestHostName": "offlinevbox",
                       "network": {"mode": "static", "address": "10.0.1.19/24",
                                   "gateway": "10.0.1.1", "dns": ["10.0.1.1"]},
                       "spec": {"vcpus": 4, "memoryMiB": 4096, "diskGiB": 32}}
        for mode in ("static", "dhcp"):
            self.target["network"]["mode"] = mode
            destination = self.root / mode
            with patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(self.image))), \
                    patch("exporter.subprocess.check_output", side_effect=self.nix_output):
                result = export(self.recipe, "vbox", self.target, destination)
            text = (destination / "VIRTUALBOX.txt").read_text()
            for value in ("UEFI", "SATA", "Bridged Adapter", "NAT", "clonemedium", "--ssh", "Get-FileHash"):
                self.assertIn(value, text)
            self.assertIn("10.0.1.19/24" if mode == "static" else "DHCP", text)
            self.assertEqual(result["artifact"], "nixberry-vbox.vdi")
            self.assertIn("VIRTUALBOX.txt", (destination / "README.txt").read_text())
            for line in (destination / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)

    def test_working_recipe_changes_do_not_change_exported_image_metadata_or_recipe(self):
        destination = self.root / "bundle"
        commands = []

        def build(command, **kwargs):
            commands.append(command)
            (self.recipe / "flake.lock").write_text('{"pinned":"SYSTEM-B"}')
            (self.recipe / "README.md").write_text("SYSTEM-B")
            self.target["spec"] = {"uuid": "SYSTEM-B"}
            return SimpleNamespace(stdout=str(self.image))

        frozen_target = json.loads(json.dumps(self.target))

        def output(command, **kwargs):
            if "#nixberryDeployment.targets." in command[-1]:
                return json.dumps(frozen_target)
            return self.nix_output(command, **kwargs)

        stale_cli_target = {**self.target, "backend": "hyperv", "spec": {"uuid": "OLD-CLI"}}
        with patch("exporter.subprocess.run", side_effect=build), \
                patch("exporter.subprocess.check_output", side_effect=output) as evaluate:
            result = export(self.recipe, "lab", stale_cli_target, destination)
        self.assertEqual(result["backend"], "libvirt")
        self.assertEqual(result["spec"], {"uuid": "test"})
        self.assertEqual((destination / "recipe/README.md").read_text(), "Návod k offline startu")
        self.assertEqual((destination / "nixberry-lab.qcow2").read_bytes(), b"prepared-disk")
        self.assertEqual(result["lockSha256"], hashlib.sha256((destination / "recipe/flake.lock").read_bytes()).hexdigest())
        build_command = next(command for command in commands if command[:2] == ["nix", "build"])
        frozen_flake = build_command[-1].split("#")[0]
        self.assertNotEqual(frozen_flake, "path:" + str(self.recipe))
        for call in evaluate.call_args_list[1:]:
            self.assertTrue(call.args[0][-1].startswith(frozen_flake + "#"))

    def test_snapshot_failure_does_not_build_or_publish(self):
        destination = self.root / "bundle"
        with patch("exporter.subprocess.check_output", side_effect=RuntimeError("Chybí cíl")), \
                patch("exporter.subprocess.run") as build, self.assertRaises(RuntimeError):
            export(self.recipe, "lab", self.target, destination)
        build.assert_not_called()
        self.assertFalse(destination.exists())

    def test_failed_build_leaves_no_partial_bundle(self):
        destination = self.root / "bundle"
        with patch("exporter.subprocess.check_output", side_effect=self.nix_output), \
                patch("exporter.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["nix", "build"])), \
                self.assertRaises(subprocess.CalledProcessError):
            export(self.recipe, "lab", self.target, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".nixberry-export-*")))

    def test_readonly_nix_snapshot_can_be_published(self):
        self.snapshot.chmod(0o555)
        destination = self.root / "bundle"
        with patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(self.image))), \
                patch("exporter.subprocess.check_output", side_effect=self.nix_output):
            export(self.recipe, "lab", self.target, destination)
        self.assertTrue((destination / "recipe/flake.lock").is_file())

    @unittest.skipUnless(shutil.which("nix"), "Integrační zkouška potřebuje Nix")
    def test_real_nix_export_uses_one_snapshot_despite_worktree_changes(self):
        (self.recipe / "image").mkdir()
        (self.recipe / "image/system.qcow2").write_bytes(b"SYSTEM-A")
        (self.recipe / "flake.lock").write_text(json.dumps({
            "version": 7, "root": "root", "nodes": {"root": {}}}))
        (self.recipe / "flake.nix").write_text('''{
          outputs = { self }: {
            nixberryImages.lab = ./image;
            nixberrySystems.lab.config.system.build.toplevel.outPath = "/nix/store/SYSTEM-A";
            nixberryDeployment.targets.lab = {
              backend = "libvirt"; profile = "fixture-A"; spec.identity = "A";
            };
          };
        }''')
        real_run = subprocess.run
        roots = []

        def execute(command, **kwargs):
            if command[:2] == ["nix", "build"]:
                (self.recipe / "README.md").write_text("SYSTEM-B")
                (self.recipe / "image/system.qcow2").write_bytes(b"SYSTEM-B")
                (self.recipe / "flake.nix").write_text("{}")
            result = real_run(command, **kwargs)
            if command[0] == "nix-store":
                roots.append(Path(command[command.index("--add-root") + 1]))
                self.assertTrue(roots[-1].resolve(strict=True).is_dir())
            if command[:2] == ["nix", "build"]:
                roots.append(Path(command[command.index("--out-link") + 1]))
                self.assertEqual(roots[-1].resolve(strict=True), Path(result.stdout.strip()))
                self.assertTrue(all(root.exists() for root in roots))
            return result

        destination = self.root / "bundle"
        with patch("exporter.subprocess.run", side_effect=execute):
            result = export(self.recipe, "lab", {**self.target, "profile": "STALE-CLI"}, destination)
        self.assertEqual(result["systemPath"], "/nix/store/SYSTEM-A")
        self.assertEqual(result["profile"], "fixture-A")
        self.assertEqual((destination / "recipe/README.md").read_text(), "Návod k offline startu")
        self.assertEqual((destination / "nixberry-lab.qcow2").read_bytes(), b"SYSTEM-A")
        self.assertEqual((self.recipe / "README.md").read_text(), "SYSTEM-B")
        self.assertEqual(len(roots), 2)
        self.assertTrue(all(not root.is_symlink() for root in roots))
        self.assertFalse((destination / "source").exists())
        self.assertFalse((destination / "image").exists())
        for line in (destination / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)

    def test_existing_destination_is_preserved(self):
        destination = self.root / "existing"
        destination.mkdir()
        (destination / "keep").write_text("data")
        with patch("exporter.subprocess.run") as build, self.assertRaises(RuntimeError):
            export(self.recipe, "lab", self.target, destination)
        build.assert_not_called()
        self.assertEqual((destination / "keep").read_text(), "data")

    def test_destination_inside_recipe_is_rejected(self):
        with self.assertRaises(RuntimeError):
            export(self.recipe, "lab", self.target, self.recipe / "nested")

    def test_dry_run_does_not_build_or_write(self):
        destination = self.root / "bundle"
        with patch("exporter.subprocess.run") as build:
            result = export(self.recipe, "lab", self.target, destination, dry_run=True)
        self.assertIn("nixberryImages.lab", result["build"][-1])
        self.assertFalse(destination.exists())
        build.assert_not_called()

    def test_each_backend_exports_the_selected_target(self):
        for backend in ("libvirt", "hyperv", "rpi"):
            with self.subTest(backend=backend):
                result = export(self.recipe, "another-target", {**self.target, "backend": backend},
                                self.root / "bundle", dry_run=True)
                self.assertTrue(result["build"][-1].endswith("#nixberryImages.another-target"))


if __name__ == "__main__":
    unittest.main()
