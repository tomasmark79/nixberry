"""Obě metody zachovávají identitu VM, ochranu disků i offline přenos."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import host_agent as agent
import portable
from exporter import export
from runtime import deployment_method
from scaffold import new_vm
from test_portable import spec


class MethodTests(unittest.TestCase):
    def test_new_declarations_default_to_image_and_persist_explicit_iso_for_every_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "flake.nix").touch()
            (root / "instances").mkdir()
            (root / "instances/default.nix").touch()
            (root / "configurations").mkdir()
            (root / "configurations/default.nix").write_text("{}")
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                for method in (None, "iso", "image"):
                    _, text = new_vm(root, {}, {}, "test", backend, offline=True, write=False, method=method)
                    self.assertIn('deploymentMethod = "' + (method or "image") + '";', text)
                self.assertEqual(deployment_method({"backend": backend}), "image")

    def test_iso_upload_uses_separate_media_and_cannot_switch_method(self):
        with tempfile.TemporaryDirectory() as root:
            s = spec(root)
            directory = Path(root) / s["name"]
            directory.mkdir()
            data = b"prepared-iso"
            (directory / "nixberry.json").write_text(json.dumps({"spec": s, "method": "iso",
                "sha256": hashlib.sha256(data).hexdigest(), "imageBytes": len(data)}))
            with patch.object(agent, "host_check", return_value=directory), \
                    patch.object(agent.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data))):
                request = {"spec": s, "backend": "vmware", "action": "upload", "method": "iso"}
                agent.dispatch(request)
                self.assertEqual((directory / "installer.iso").read_bytes(), data)
                self.assertFalse((directory / "system.vmdk").exists())
                with self.assertRaisesRegex(RuntimeError, "The method differs"):
                    agent.dispatch({**request, "method": "image", "action": "start"})

    def test_iso_creation_allocates_empty_disk_and_attaches_iso_on_portable_platforms(self):
        for backend in ("proxmox", "virtualbox", "vmware"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as root:
                s = spec(root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
                directory = Path(root) / s["name"]
                directory.mkdir()
                media = b"iso"
                (directory / "installer.iso").write_bytes(media)
                (directory / "nixberry.json").write_text(json.dumps({"spec": s, "method": "iso",
                    "sha256": hashlib.sha256(media).hexdigest(), "imageBytes": len(media)}))
                def run(args, **kwargs):
                    if args[0] == "pvesm":
                        return str(Path(root) / "managed.iso")
                    if args[0] == "pvesh":
                        if args[2] == "/storage/local":
                            return '{"content":"iso"}'
                        if args[2] == "/nodes/pve/storage/local/status":
                            return '{"active":1,"enabled":1}'
                        return '{"virtio0":"local-lvm:vm-1000-disk-1,size=32G","efidisk0":"local-lvm:vm-1000-disk-0"}'
                    return ""
                with patch.object(agent, "run", side_effect=run) as call, \
                        patch.object(agent, "proxmox_registry", return_value=[]), \
                        patch.object(agent, "probe", return_value={"state": "shut off", "addresses": []}):
                    agent.create(s, backend, directory)
                commands = [c.args[0] for c in call.call_args_list]
                if backend == "proxmox":
                    create = next(c for c in commands if c[:2] == ["qm", "create"])
                    self.assertEqual(create[create.index("--virtio0") + 1], "local-lvm:32")
                    self.assertNotIn("import-from=", " ".join(create))
                    self.assertIn("local:iso/nixberry-" + s["uuid"] + ".iso,media=cdrom", create)
                    self.assertEqual((Path(root) / "managed.iso").read_bytes(), media)
                elif backend == "virtualbox":
                    self.assertTrue(any("createmedium" in c for c in commands))
                    self.assertTrue(any("dvddrive" in c and str(directory / "installer.iso") in c for c in commands))
                else:
                    self.assertTrue(any(c[0].endswith("vmware-vdiskmanager") and "-c" in c for c in commands))
                    self.assertIn('sata0:1.fileName = "installer.iso"', (directory / (s["name"] + ".vmx")).read_text())

    def test_foreign_iso_is_never_detached(self):
        s = spec("/tmp", bridge="vmbr0")
        with patch.object(agent, "owned", return_value={"method": "iso"}), \
                patch.object(agent, "probe", return_value={"state": "shut off"}), \
                patch.object(agent, "run", return_value='"SATA-1-0"="/foreign.iso"') as run:
            with self.assertRaisesRegex(RuntimeError, "expected ISO"):
                agent.installer_media(s, "virtualbox", Path("/tmp/own-vm"), detach=True)
            self.assertEqual(run.call_count, 1)

    def test_failed_installer_does_not_eject_iso_or_restart_vm(self):
        with tempfile.TemporaryDirectory() as root:
            target = {"backend": "vmware", "deploymentMethod": "iso", "spec": spec(root)}
            snapshot = {"state": "running", "addresses": ["192.168.79.200"]}
            result = SimpleNamespace(returncode=0)
            failed = SimpleNamespace(returncode=1)
            with patch.object(portable, "rpc", return_value=snapshot) as rpc, \
                    patch.object(portable.subprocess, "run", side_effect=[result, result, failed]), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "Installation failed"):
                    portable.install_iso(target, {"systemPath": "/nix/store/system", "diskoPath": "/nix/store/disko"}, apply=True)
            self.assertEqual([c.args[1] for c in rpc.call_args_list], ["installer-check"] * 3)

    def test_offline_iso_exports_installer_script_metadata_and_valid_hashes_for_all_vm_backends(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                recipe, image = root / "recipe", root / "iso"
                recipe.mkdir(); image.mkdir()
                (recipe / "flake.lock").write_text("{}")
                (image / "installer.iso").write_bytes(b"iso-with-system")
                target = {"backend": backend, "profile": "nixberry-vm" if backend == "libvirt" else "nixberry-" + backend,
                          "spec": {"diskGiB": 32, "vcpus": 2, "memoryMiB": 4096}}
                def output(command, **kwargs):
                    if command[1:3] == ["flake", "metadata"]: return json.dumps({"path": str(recipe)})
                    if "#nixberryDeployment.targets." in command[-1]: return json.dumps(target)
                    return "/nix/store/disko" if "diskoScript" in command[-1] else "/nix/store/system"
                with patch("exporter.subprocess.check_output", side_effect=output), \
                        patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(image))) as run, \
                        contextlib.redirect_stderr(io.StringIO()):
                    manifest = export(recipe, "test", target, root / "bundle", method="iso")
                self.assertEqual(manifest["format"], "iso")
                self.assertEqual(manifest["deploymentMethod"], "iso")
                self.assertIn("#nixberryInstallers.test", run.call_args.args[0][-1])
                bundle = root / "bundle"
                self.assertIn("nixos-install", (bundle / "INSTALL-ISO.sh").read_text())
                subprocess.run(["bash", "-n", str(bundle / "INSTALL-ISO.sh")], check=True)
                for line in (bundle / "SHA256SUMS").read_text().splitlines():
                    digest, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256((bundle / name).read_bytes()).hexdigest(), digest)
