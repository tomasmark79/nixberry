"""Lehký přehled nevynucuje systém VM; spuštění nástroje zachová režim zápisu."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dispatch import launch
from scaffold import new_vm


class DispatchTests(unittest.TestCase):
    def test_lazy_tool_preserves_target_and_explicit_mode(self):
        recipe = Path(__file__).resolve().parents[2]
        for mode in ("--dry-run", "--apply"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"NIXBERRY_RECIPE": str(recipe), "NIXBERRY_DISPATCH_TARGET": "/expected"}), \
                    patch("dispatch.Path.read_text", return_value='{"spec":{"uuid":"identity"}}'), \
                    patch("dispatch.freeze_recipe", return_value="/nix/store/frozen-recipe") as freeze, patch("dispatch.subprocess.run") as run:
                run.return_value.returncode = 7
                self.assertEqual(launch("lab", "nixberry-vm-provision", [mode]), 7)
                self.assertEqual(run.call_args.args[0], [
                    "nix", "shell", "--no-write-lock-file", "path:/nix/store/frozen-recipe#nixberryTools.lab",
                    "--command", "nixberry-vm-provision", mode])
                self.assertEqual(freeze.call_args.args[:3], (str(recipe), "lab", {"spec": {"uuid": "identity"}}))

    def test_changed_identity_stops_before_loading_or_running_tool(self):
        recipe = Path(__file__).resolve().parents[2]
        with patch.dict(os.environ, {"NIXBERRY_RECIPE": str(recipe), "NIXBERRY_DISPATCH_TARGET": "/expected"}), \
                patch("dispatch.Path.read_text", return_value="{}"), \
                patch("dispatch.freeze_recipe", side_effect=RuntimeError("changed identity")), \
                patch("dispatch.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "changed identity"):
                launch("lab", "nixberry-vm-provision", ["--apply"])
            run.assert_not_called()

    def test_method_selects_frozen_tool_without_changing_identity_or_forwarding_local_option(self):
        recipe = Path(__file__).resolve().parents[2]
        with patch.dict(os.environ, {"NIXBERRY_RECIPE": str(recipe), "NIXBERRY_DISPATCH_TARGET": "/expected"}), \
                patch("dispatch.Path.read_text", return_value='{"spec":{"uuid":"identity"}}'), \
                patch("dispatch.freeze_recipe", return_value="/nix/store/frozen") as freeze, \
                patch("dispatch.subprocess.run", return_value=type("Result", (), {"returncode": 0})()) as run:
            launch("lab", "nixberry-vm-provision", ["--method", "iso", "--apply"])
        self.assertIn("path:/nix/store/frozen#nixberryToolsByMethod.iso.lab", run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-2:], ["nixberry-vm-provision", "--apply"])
        self.assertEqual(freeze.call_args.args[2], {"spec": {"uuid": "identity"}})

    def test_missing_recipe_never_runs_nix(self):
        with patch.dict(os.environ, {}, clear=True), patch("dispatch.subprocess.run") as run:
            with self.assertRaises(RuntimeError):
                launch("lab", "nixberry-vm-provision", ["--apply"])
            run.assert_not_called()

    def test_cli_does_not_evaluate_broken_vm_system(self):
        recipe = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recipe"
            from test_support import copy_test_recipe
            copy_test_recipe(recipe, root)
            # Test má vlastní deklaraci a nezávisí na soukromých testovacích VM.
            new_vm(root, {}, {}, "vm-demo-lazy-evaluation", "vmware", offline=True)
            (root / "platforms/vm/configuration.nix").write_text('throw "NIXBERRY_TEST_NO_SYSTEM_EVALUATION"')
            expression = ('let f = builtins.getFlake ' + json.dumps("path:" + str(root)) + '; in { '
                          'cli = f.packages.x86_64-linux.nixberry.drvPath; '
                          'system = (builtins.tryEval f.nixberrySystems.vm-demo-lazy-evaluation.config.system.build.toplevel.outPath).success; }')
            output = subprocess.check_output(["nix", "eval", "--impure", "--json", "--expr", expression], text=True)
            result = json.loads(output)
            self.assertFalse(result["system"])
            self.assertTrue(result["cli"].endswith(".drv"))
