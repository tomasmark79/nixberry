"""Encryption policy, artifact binding and refusal before side effects."""

import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from security import FORMATS, artifact, binding, digest, policy, prepare_image, read_password, require_supported, unlock, unlock_for_install, validate_manifest
from scaffold import new_vm
from test_support import copy_test_recipe


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = {"backend": "libvirt", "spec": {"uuid": "12345678-1234-4321-8123-123456789abc",
            "name": "nixberry-vm-demo-test", "diskGiB": 16}, "security": {"mode": "luks",
            "imageDirectory": str(self.root)}, "network": {"mode": "dhcp"}}
        self.disk = self.root / "system.qcow2"
        self.disk.write_bytes(b"encrypted artifact fixture")
        self.metadata = {"binding": binding(self.target), "systemPath": "/nix/store/expected-system",
                         "initrdHostKey": "ssh-ed25519 TESTKEY", "sha256": digest(self.disk)}
        self.save_metadata()

    def save_metadata(self):
        (self.root / "encryption.json").write_text(json.dumps(self.metadata))

    def test_all_vm_backends_refuse_confidential_and_encrypted_iso(self):
        with patch("security.subprocess.run") as run:
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                for offline in (False, True):
                    target = {**self.target, "backend": backend, "offline": offline}
                    self.assertEqual(require_supported(target)["mode"], "luks")
                    with self.assertRaisesRegex(RuntimeError, "Encrypted ISO"):
                        require_supported(target, "iso")
                    target["security"] = {"mode": "confidential"}
                    for method in ("iso", "image"):
                        with self.assertRaisesRegex(RuntimeError, "attestation-bound"):
                            require_supported(target, method)
            run.assert_not_called()

    def test_no_silent_fallback_or_unrecognized_secret_fields(self):
        self.assertEqual(policy({})["mode"], "none")
        for security in ({"mode": "typo"}, {"mode": "luks", "unlock": "attested"},
                         {"mode": "confidential", "unlock": "manual"}, {"port": True},
                         {"mode": "luks", "password": "not-allowed"}, {"mode": "luks", "port": 22}):
            with self.subTest(security=security), self.assertRaises(ValueError):
                policy({"security": security})
        with self.assertRaisesRegex(ValueError, "Raspberry Pi"):
            require_supported({"backend": "rpi", "security": {"mode": "luks"}})

    def test_missing_boot_verification_does_not_claim_a_proven_locked_disk(self):
        manifest = {"security": self.target["security"], "securityTarget": self.target}
        with patch("sys.stdin.isatty", return_value=False), patch("security.unlock") as connect:
            with self.assertRaisesRegex(ValueError, "boot is not verified"):
                unlock_for_install(manifest, None)
            connect.assert_not_called()

    def test_artifact_rejects_changes_identity_policy_system_and_symlinks(self):
        self.assertEqual(artifact(self.target, system=self.metadata["systemPath"])[0], self.disk)
        for key in ("uuid", "diskGiB"):
            changed = copy.deepcopy(self.target)
            changed["spec"][key] = "different"
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                artifact(changed)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            artifact(self.target, system="/nix/store/other-system")
        self.disk.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            artifact(self.target)
        self.disk.unlink()
        self.disk.symlink_to("encryption.json")
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            artifact(self.target)

    def test_direct_manifest_requires_verified_media_and_system(self):
        manifest = {"security": self.target["security"], "securityTarget": self.target,
                    "imagePath": str(self.disk), "systemPath": self.metadata["systemPath"]}
        validate_manifest(manifest)
        with self.assertRaisesRegex(RuntimeError, "media path"):
            validate_manifest({**manifest, "imagePath": "/tmp/other-image"})
        with self.assertRaisesRegex(RuntimeError, "Encrypted ISO"):
            validate_manifest({**manifest, "deploymentMethod": "iso"})

    def test_password_files_are_private_regular_and_not_symlinks(self):
        path = self.root / "password"
        path.write_bytes(b"long-enough-passphrase\n")
        path.chmod(0o600)
        self.assertEqual(read_password(path), b"long-enough-passphrase")
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            read_password(path)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            read_password(link)
        path.chmod(0o600)
        for data in (b"short", b"long-enough\nbut-multiline", b"a" * 4097):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                read_password(path)
        with patch("sys.stdin.isatty", return_value=False), patch("security.getpass.getpass") as prompt:
            with self.assertRaisesRegex(ValueError, "requires a terminal"):
                read_password()
            prompt.assert_not_called()

    def test_remote_unlock_requires_strict_jump_host_verification(self):
        target = copy.deepcopy(self.target)
        target["backend"] = "hyperv"
        target["spec"].update(sshHost="192.168.50.2", sshUser="operator")
        (self.root / "system.vhdx").write_bytes(self.disk.read_bytes())
        self.metadata["binding"] = binding(target)
        self.save_metadata()
        with patch("security.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            unlock(target, "172.20.192.20")
        proxy = next(value for value in run.call_args.args[0] if value.startswith("ProxyCommand="))
        self.assertIn("StrictHostKeyChecking=yes", proxy)
        self.assertIn("BatchMode=yes", proxy)
        self.assertIn("ControlPath=none", proxy)
        self.assertIn("operator@192.168.50.2", proxy)

    def test_prepare_dry_run_never_reads_password_builds_or_creates_destination(self):
        output = self.root / "output"
        with patch("security.subprocess.run") as run, patch("security.read_password") as read:
            result = prepare_image("/tmp/fictional-recipe", "vm-demo-test", self.target,
                                   output, key_file="/missing", dry_run=True)
            self.assertIn("#nixberryImageBuilders.vm-demo-test", result["build"][-1])
            self.assertFalse(output.exists())
            run.assert_not_called()
            read.assert_not_called()

    def test_prepare_cli_requires_explicit_recipe_without_current_directory_fallback(self):
        import cli
        target = {**self.target, "offline": True, "host": "offline:vm-demo-test"}
        for recipe in ("", str(self.root / "missing-recipe")):
            with patch.dict("os.environ", {"NIXBERRY_DEPLOYMENT": "/registry", "NIXBERRY_RECIPE": recipe}, clear=True), \
                    patch("pathlib.Path.read_text", return_value=json.dumps({"vm-demo-test": target})), \
                    patch.object(cli, "prepare_image") as prepare, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.main(["prepare-image", "vm-demo-test", "--dry-run"])
                self.assertEqual(error.exception.code, 2)
                prepare.assert_not_called()

    def test_password_inside_recipe_is_rejected_before_source_capture(self):
        recipe = self.root / "recipe"
        recipe.mkdir()
        with patch("security.subprocess.run") as run, patch("security.read_password") as read:
            for path in (recipe / "password", Path("/nix/store/secret")):
                with self.assertRaisesRegex(ValueError, "outside the recipe"):
                    prepare_image(recipe, "vm-demo-test", self.target,
                                  self.root / "output", key_file=path)
            run.assert_not_called()
            read.assert_not_called()

    def test_unlock_pins_key_disables_forwarding_and_never_accepts_new_keys(self):
        with patch("security.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(unlock(self.target, "192.168.50.10"), 0)
            command = run.call_args.args[0]
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn("GlobalKnownHostsFile=/dev/null", command)
            self.assertIn("ForwardAgent=no", command)
            for option in ("KnownHostsCommand=none", "VerifyHostKeyDNS=no", "HostKeyAlgorithms=ssh-ed25519",
                           "UpdateHostKeys=no", "ControlMaster=no", "ControlPath=none", "ControlPersist=no"):
                self.assertIn(option, command)
            self.assertNotIn("StrictHostKeyChecking=accept-new", command)
            self.assertEqual(command[-1], "root@192.168.50.10")
        for address in ("192.168.79.1", "-oProxyCommand=bad", "host;bad", "fe80::1%bad'zone"):
            with self.assertRaises(ValueError):
                unlock(self.target, address)

    def test_nix_policy_rejects_unknown_fields_and_bad_types(self):
        source = Path(__file__).with_name("security.nix")
        for expression, valid in (("{}", True), ('{ mode = "luks"; }', True),
                                  ('{ mode = "confidential"; }', True),
                                  ('{ mode = "typo"; }', False), ('{ port = true; }', False),
                                  ('{ password = "secret"; }', False),
                                  ('{ mode = "luks"; port = 22; }', False),
                                  ('{ mode = "confidential"; unlock = "manual"; }', False),
                                  ('{ imageDirectory = "/nix/store/key"; }', False)):
            result = subprocess.run(["nix", "eval", "--impure", "--json", "--expr",
                f"import {source} {{ security = {expression}; }}"], text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_all_guest_profiles_have_encrypted_root_and_restricted_network_unlock(self):
        recipe = self.root / "recipe"
        copy_test_recipe(Path(__file__).parents[2], recipe)
        for backend in FORMATS:
            new_vm(recipe, {}, {}, "vm-demo-" + backend, backend, offline=True, security_mode="luks")
        expression = '''let f = builtins.getFlake "path:RECIPE"; in builtins.mapAttrs (_: s:
          let c = s.config; in {
            root = c.fileSystems."/".device;
            keyFile = c.boot.initrd.luks.devices.nixberry-root.keyFile;
            discards = c.boot.initrd.luks.devices.nixberry-root.allowDiscards;
            port = c.boot.initrd.network.ssh.port;
            authorizedKeys = c.boot.initrd.network.ssh.authorizedKeys;
            swap = c.swapDevices;
            drivers = c.boot.initrd.availableKernelModules;
            system = c.system.build.toplevel.drvPath;
          }) f.nixberrySystems'''.replace("RECIPE", str(recipe))
        result = subprocess.run(["nix", "eval", "--impure", "--json", "--expr", expression],
                                text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        for name, config in json.loads(result.stdout).items():
            with self.subTest(target=name):
                self.assertEqual(config["root"], "/dev/mapper/nixberry-root")
                self.assertIsNone(config["keyFile"])
                self.assertFalse(config["discards"])
                self.assertEqual(config["swap"], [])
                self.assertEqual(config["port"], 2222)
                self.assertTrue(all(key.startswith('restrict,pty,command="systemctl default" ')
                                    for key in config["authorizedKeys"]))
                self.assertTrue(config["authorizedKeys"])
                self.assertTrue({"virtio_net", "e1000", "vmxnet3", "hv_netvsc"} <= set(config["drivers"]))
