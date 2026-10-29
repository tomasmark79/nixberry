#!/usr/bin/env python3
"""Ochrany aktualizace, přenos přes Windows a potvrzení skutečné aktivace."""

import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import updater


OLD = "/nix/store/" + "a" * 32 + "-nixos-system-old-26.05"
NEW = "/nix/store/" + "b" * 32 + "-nixos-system-new-26.05"
SNAPSHOT = {"state": "running", "addresses": ["172.21.222.76"]}
HYPERV = {"host": "windows", "backend": "hyperv", "guestHostName": "nixberry-vm", "spec": {
    "sshHost": "192.168.50.10", "sshUser": "operator", "ownerId": "test-owner"}}
RPI = {"host": "pi", "backend": "rpi", "spec": {"diskSerial": "ssd-serial"}}
LIBVIRT = {"host": "linux", "backend": "libvirt", "spec": {
    "uuid": "315b9a43-1653-4b2f-9cd9-790081000001"}}


class UpdateChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.recipe = directory.name
        for filename in ("flake.nix", "flake.lock"):
            Path(self.recipe, filename).touch()
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.messages = io.StringIO()
        redirect = contextlib.redirect_stderr(self.messages)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def dependencies(self):
        fixtures = {
            "freeze_recipe": Mock(return_value=self.recipe),
            "status": Mock(return_value=SNAPSHOT),
            "preflight": Mock(return_value=OLD),
            "build": Mock(return_value=NEW),
            "verify": Mock(return_value={"verified": True, "hostname": "nixberry-vm",
                                         "system": NEW, "root": "/dev/sda2",
                                         "addresses": SNAPSHOT["addresses"]}),
            "reboot_required": Mock(return_value=True),
        }
        for name, mock in fixtures.items():
            patcher = patch.object(updater, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(updater.subprocess, "run", return_value=Mock(returncode=0))
        runner = patcher.start()
        self.addCleanup(patcher.stop)
        return fixtures, runner

    def test_default_and_explicit_plan_are_offline_for_every_backend(self):
        fixtures, runner = self.dependencies()
        for target in (RPI, LIBVIRT, HYPERV):
            self.assertIsNone(updater.update(self.recipe, "test", target))
            self.assertIsNone(updater.update(self.recipe, "test", target, apply=False))
        runner.assert_not_called()
        for mock in fixtures.values():
            mock.assert_not_called()
        self.assertIn("nixberry update test --apply", self.messages.getvalue())
        self.assertEqual(self.output.getvalue(), "")

    def test_invalid_recipe_backend_and_name_never_connect(self):
        fixtures, runner = self.dependencies()
        for recipe, name, target in (("/missing", "test", RPI), (self.recipe, "a;reboot", RPI),
                                     (self.recipe, "test", {**RPI, "backend": "unknown"})):
            with self.assertRaises(RuntimeError):
                updater.update(recipe, name, target, apply=True)
        fixtures["status"].assert_not_called()
        runner.assert_not_called()

    def test_hyperv_uses_exact_instance_system_and_jump_host(self):
        fixtures, runner = self.dependencies()
        with patch.dict(os.environ, {"NIX_SSHOPTS": "-o UnsafeAmbientOption=yes"}):
            result = updater.update(self.recipe, "musicbox-test", HYPERV, apply=True)
        fixtures["build"].assert_called_once()
        self.assertEqual(fixtures["build"].call_args.args[:2], (self.recipe, "musicbox-test"))
        copy, activate = runner.call_args_list
        self.assertEqual(copy.args[0], ["nix", "copy", "--to", "ssh://root@172.21.222.76", NEW])
        options = shlex.split(copy.kwargs["env"]["NIX_SSHOPTS"])
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertIn("BatchMode=yes", options)
        self.assertEqual(options[-2:], ["-J", "operator@192.168.50.10"])
        self.assertNotIn("UnsafeAmbientOption=yes", options)
        command = activate.args[0]
        self.assertIn("root@172.21.222.76", command)
        script = shlex.split(command[-1])
        self.assertEqual(script[:4], ["systemd-run", "--unit=nixberry-update", "--collect", "--wait"])
        self.assertIn("--property=Type=oneshot", script)
        self.assertIn("--setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin", script)
        self.assertIn(OLD, script[-1])
        self.assertIn("nix-env -p /nix/var/nix/profiles/system --set " + NEW, script[-1])
        self.assertIn(NEW + "/bin/switch-to-configuration switch", script[-1])
        self.assertNotIn("reboot", script[-1])
        fixtures["verify"].assert_called_once_with(HYPERV, NEW)
        self.assertTrue(result["rebootRequired"])
        self.assertIn("nixberry ssh musicbox-test -- reboot", self.messages.getvalue())
        self.assertEqual(self.output.getvalue(), NEW + "\n")

    def test_rpi_and_libvirt_copy_directly_and_do_not_require_reboot(self):
        fixtures, runner = self.dependencies()
        fixtures["reboot_required"].return_value = False
        for name, target in (("nixberry", RPI), ("second", LIBVIRT)):
            runner.reset_mock()
            result = updater.update(self.recipe, name, target, apply=True)
            copy = runner.call_args_list[0]
            self.assertNotIn("-J", shlex.split(copy.kwargs["env"]["NIX_SSHOPTS"]))
            self.assertFalse(result["rebootRequired"])

    def test_direct_guest_override_preserves_port_for_copy_activation_and_hints(self):
        fixtures, runner = self.dependencies()
        target = {"host": "offline:test", "backend": "proxmox", "offline": True, "spec": {},
                  "_guestConnection": {"host": "203.0.113.19", "user": "root", "port": 2030}}
        updater.update(self.recipe, "test", target, apply=False)
        runner.assert_not_called()
        for mock in fixtures.values():
            mock.assert_not_called()
        self.assertIn("nixberry update test --ssh root@203.0.113.19:2030 --apply", self.messages.getvalue())
        updater.update(self.recipe, "test", target, apply=True)
        copy, activation = runner.call_args_list
        self.assertEqual(copy.args[0][3], "ssh://root@203.0.113.19")
        options = shlex.split(copy.kwargs["env"]["NIX_SSHOPTS"])
        self.assertEqual(options[options.index("-p") + 1], "2030")
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertEqual(activation.args[0][activation.args[0].index("-p") + 1], "2030")
        self.assertIn("nixberry verify test --ssh root@203.0.113.19:2030", self.messages.getvalue())

    def test_unreachable_or_wrong_machine_stops_before_build(self):
        fixtures, runner = self.dependencies()
        fixtures["status"].side_effect = RuntimeError("Cizí VM")
        with self.assertRaisesRegex(RuntimeError, "Cizí VM"):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        fixtures["build"].assert_not_called()
        runner.assert_not_called()

    def test_recovery_environment_stops_before_build(self):
        fixtures, runner = self.dependencies()
        fixtures["preflight"].side_effect = RuntimeError("Obnovovací SD")
        with self.assertRaisesRegex(RuntimeError, "Obnovovací SD"):
            updater.update(self.recipe, "test", RPI, apply=True)
        fixtures["build"].assert_not_called()
        runner.assert_not_called()

    def test_build_failure_never_transfers_or_activates(self):
        fixtures, runner = self.dependencies()
        fixtures["build"].side_effect = subprocess.CalledProcessError(1, ["nix", "build"])
        with self.assertRaises(subprocess.CalledProcessError):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        runner.assert_not_called()

    def test_system_change_during_build_stops_before_transfer(self):
        fixtures, runner = self.dependencies()
        fixtures["preflight"].side_effect = [OLD, NEW]
        with self.assertRaisesRegex(RuntimeError, "during the build"):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        runner.assert_not_called()

    def test_copy_failure_never_activates(self):
        fixtures, runner = self.dependencies()
        runner.side_effect = subprocess.CalledProcessError(1, ["nix", "copy"])
        with self.assertRaises(subprocess.CalledProcessError):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        self.assertEqual(runner.call_count, 1)
        fixtures["verify"].assert_not_called()

    def test_ip_change_after_copy_prevents_activation(self):
        fixtures, runner = self.dependencies()
        fixtures["status"].side_effect = [SNAPSHOT, SNAPSHOT,
                                          {"state": "running", "addresses": ["10.0.0.3"]}]
        with self.assertRaisesRegex(RuntimeError, "during transfer"):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        self.assertEqual(runner.call_count, 1)

    def test_system_change_after_copy_prevents_activation(self):
        fixtures, runner = self.dependencies()
        fixtures["preflight"].side_effect = [OLD, OLD, NEW]
        with self.assertRaisesRegex(RuntimeError, "during transfer"):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        self.assertEqual(runner.call_count, 1)

    def test_activation_failure_reports_partial_state(self):
        fixtures, runner = self.dependencies()
        runner.side_effect = [Mock(returncode=0), Mock(returncode=1)]
        with self.assertRaisesRegex(RuntimeError, "generation may already have been set"):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        fixtures["verify"].assert_not_called()

    def test_failed_verification_does_not_claim_success(self):
        fixtures, runner = self.dependencies()
        fixtures["verify"].side_effect = RuntimeError("Neaktivní sshd")
        with self.assertRaisesRegex(RuntimeError, "Activation completed.*Neaktivní sshd"):
            updater.update(self.recipe, "test", HYPERV, apply=True)
        self.assertNotIn("Verified:", self.messages.getvalue())
        self.assertEqual(self.output.getvalue(), "")

    def test_protected_vm_address_is_rejected_before_ssh_or_build(self):
        with patch.object(updater, "freeze_recipe", return_value=self.recipe), \
                patch.object(updater, "status", return_value={
                "state": "running", "addresses": ["192.168.79.1"]}), \
                patch.object(updater, "read") as read, patch.object(updater, "build") as build:
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                with self.subTest(backend=backend):
                    target = {**LIBVIRT, "backend": backend,
                              "spec": {**LIBVIRT["spec"], "mac": "02:00:00:00:00:01"}}
                    with self.assertRaises(RuntimeError):
                        updater.update(self.recipe, "test", target, apply=True)
            read.assert_not_called()
            build.assert_not_called()

    def test_stale_cli_stops_before_connecting_or_building(self):
        fixtures, runner = self.dependencies()
        fixtures["freeze_recipe"].side_effect = RuntimeError("The target declaration differs")
        with self.assertRaisesRegex(RuntimeError, "The target declaration differs"):
            updater.update(self.recipe, "test", RPI, apply=True)
        fixtures["status"].assert_not_called()
        fixtures["build"].assert_not_called()
        runner.assert_not_called()

    def test_build_uses_frozen_recipe(self):
        fixtures, _ = self.dependencies()
        fixtures["freeze_recipe"].return_value = "/nix/store/frozen-recipe"
        updater.update(self.recipe, "test", RPI, apply=True)
        self.assertEqual(fixtures["build"].call_args.args[0], "/nix/store/frozen-recipe")

    @unittest.skipUnless(shutil.which("nix"), "Integrační zkouška potřebuje Nix")
    def test_real_snapshot_survives_recipe_changes_and_rejects_stale_identity(self):
        recipe = Path(self.recipe)
        (recipe / "flake.lock").write_text(json.dumps({
            "version": 7, "root": "root", "nodes": {"root": {}}}))
        (recipe / "flake.nix").write_text('''{
          outputs = { self }: {
            nixberryDeployment.targets.test = {
              host = "pi"; backend = "rpi"; spec.diskSerial = "ssd-serial";
            };
          };
        }''')
        with tempfile.TemporaryDirectory() as directory:
            target = {**RPI, "commands": {"probe": "/old-cli/probe"}, "operations": ["update"],
                      "capabilities": {"build": True, "export": True, "system": True, "hypervisor": False},
                      "_guestConnection": {"host": "example", "port": 2030, "user": "root"}}
            frozen = updater.freeze_recipe(self.recipe, "test", target, directory)
            self.assertEqual((Path(directory) / "recipe").resolve(), Path(frozen))
            (recipe / "flake.nix").write_text("{}")
            declared = json.loads(subprocess.check_output([
                "nix", "eval", "--json", "--no-write-lock-file",
                "path:" + frozen + "#nixberryDeployment.targets.test"], text=True))
            self.assertEqual(declared, RPI)
            with tempfile.TemporaryDirectory() as second, self.assertRaisesRegex(
                    RuntimeError, "The target declaration differs"):
                updater.freeze_recipe(frozen, "test", {**RPI, "host": "other"}, second)

    def test_preflight_checks_disk_and_architecture_without_new_hostname(self):
        for target, disk, arch in ((RPI, "ssd-serial", "aarch64"),
                                   (LIBVIRT, "/dev/vda2", "x86_64"),
                                   (HYPERV, "/dev/sda2", "x86_64")):
            with patch.object(updater, "read", return_value=OLD) as read:
                self.assertEqual(updater.preflight(target, SNAPSHOT), OLD)
                script = read.call_args.args[0][-1]
                for value in (disk, arch, "FSTYPE /boot", "mountpoint -q /iso"):
                    self.assertIn(value, script)
                self.assertNotIn("hostname", script)
                self.assertNotIn("systemctl --failed", script)

    def test_store_path_must_be_one_system_generation(self):
        self.assertEqual(updater.system_path(NEW), NEW)
        for value in ("", NEW + "\n" + OLD, NEW + "; reboot", "/tmp/system", "/nix/store/a-system"):
            with self.assertRaises(RuntimeError):
                updater.system_path(value)

    def test_build_keeps_gc_root_and_lock_and_selects_exact_target(self):
        with patch.object(updater.subprocess, "run") as run, \
                patch.object(Path, "resolve", return_value=Path(NEW)) as resolve:
            self.assertEqual(updater.build(self.recipe, "second", "/tmp/build"), NEW)
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["nix", "build", "--no-write-lock-file", "--out-link", "/tmp/build/system"])
        self.assertEqual(command[-1], "path:" + self.recipe + "#nixberrySystems.second.config.system.build.toplevel")
        self.assertTrue(run.call_args.kwargs["check"])
        resolve.assert_called_once_with(strict=True)

    def test_reboot_check_compares_actual_boot_artifacts(self):
        for value, expected in (("yes", True), ("no", False)):
            with patch.object(updater, "read", return_value=value) as read:
                self.assertEqual(updater.reboot_required(["ssh", "host"]), expected)
                script = read.call_args.args[0][-1]
                self.assertIn("/run/booted-system/kernel", script)
                self.assertIn("/run/booted-system/initrd", script)
                self.assertIn("cmp -s /run/booted-system/kernel-params", script)
        with patch.object(updater, "read", return_value="unknown"), self.assertRaises(RuntimeError):
            updater.reboot_required(["ssh", "host"])


if __name__ == "__main__":
    unittest.main()
