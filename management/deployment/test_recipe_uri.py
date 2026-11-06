"""Recipe references preserve local paths and ignored declarations."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from exporter import export
from security import prepare_image
from test_support import copy_test_recipe
from updater import freeze_recipe


class RecipeUriChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nixberry-uri-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = Path(__file__).resolve().parents[2]
        self.recipe = self.root / "recipe with space#and%20?query-ž"
        copy_test_recipe(self.source, self.recipe)
        self.declaration = "machines/vm/vm-demo-uri.nix"
        (self.recipe / self.declaration).write_text('{ backend = "libvirt"; spec.name = "vm-demo-uri"; }')
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "--quiet", str(self.recipe)], check=True)
        subprocess.run(["git", "check-ignore", "--quiet", self.declaration], cwd=self.recipe, check=True)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        # Exercise the emitted reference with real Nix without building the CLI.
        (self.tools / "nix").write_text(f'''#!{sys.executable}
import json, os, subprocess, sys
reference = sys.argv[3]
metadata = json.loads(subprocess.check_output([
    {shutil.which("nix")!r}, "flake", "metadata", "--offline", "--json",
    "--no-write-lock-file", reference.split("#", 1)[0]], text=True))
metadata.update(reference=reference, arguments=sys.argv[4:], recipe=os.environ["NIXBERRY_RECIPE"])
print(json.dumps(metadata))
''')
        (self.tools / "nix").chmod(0o755)
        self.environment = {**os.environ, "PATH": str(self.tools) + os.pathsep + os.environ["PATH"],
                            "NIXBERRY_LANG": "en"}
        self.environment.pop("NIXBERRY_RECIPE", None)

    def metadata(self, reference):
        return json.loads(subprocess.check_output([
            "nix", "flake", "metadata", "--offline", "--json", "--no-write-lock-file",
            reference.split("#", 1)[0]], text=True))

    def assert_declaration(self, metadata):
        self.assertEqual((Path(metadata["path"]) / self.declaration).read_text(),
                         (self.recipe / self.declaration).read_text())

    def test_freeze_captures_each_reserved_path_character_and_local_declaration(self):
        for name in ("recipe with space", "recipe#fragment", "recipe%20literal", "recipe?query"):
            renamed = self.root / name
            self.recipe.rename(renamed)
            self.recipe = renamed
            with self.subTest(name=name), tempfile.TemporaryDirectory() as roots:
                # Read the declaration independently of flake URI construction.
                expression = "(import (builtins.toPath " + json.dumps(
                    str(self.recipe / "management/deployment")) + ")).targets.vm-demo-uri"
                target = json.loads(subprocess.check_output([
                    "nix", "eval", "--offline", "--impure", "--json", "--expr", expression], text=True))
                frozen = freeze_recipe(str(self.recipe), "vm-demo-uri", target, roots)
                self.assert_declaration({"path": frozen})

    def test_launcher_default_and_explicit_override_preserve_recipe_and_arguments(self):
        for override in (False, True):
            environment = dict(self.environment)
            if override:
                environment["NIXBERRY_RECIPE"] = str(self.recipe)
            launcher = (self.source if override else self.recipe) / "nixberry"
            with self.subTest(override=override):
                result = subprocess.run([str(launcher), "show", "vm-demo-uri"], cwd=self.root,
                                        env=environment, text=True, capture_output=True, check=True)
                metadata = json.loads(result.stdout)
                self.assert_declaration(metadata)
                self.assertEqual(metadata["recipe"], str(self.recipe))
                self.assertEqual(metadata["arguments"], ["--", "show", "vm-demo-uri"])
                self.assertTrue(metadata["reference"].endswith("#nixberry"))

    def test_linked_configuration_bootstrap_uses_encoded_recipe_reference(self):
        external = self.root / "external"
        external.mkdir()
        (external / "default.nix").write_text("{}")
        (self.recipe / "configurations/linked").symlink_to(external, target_is_directory=True)
        result = subprocess.run([str(self.recipe / "nixberry"), "show", "vm-demo-uri"],
                                env=self.environment, text=True, capture_output=True, check=True)
        metadata = json.loads(result.stdout)
        self.assertTrue(metadata["reference"].endswith("#nixberry-source"))
        self.assertEqual(metadata["arguments"], ["--", str(self.recipe), "show", "vm-demo-uri"])

    def test_materialized_launcher_preserves_original_recipe_environment(self):
        result = subprocess.run([sys.executable, str(self.recipe / "management/deployment/recipe_source.py"),
                                 str(self.recipe), "show", "vm-demo-uri"], env=self.environment,
                                text=True, capture_output=True, check=True)
        metadata = json.loads(result.stdout)
        self.assert_declaration(metadata)
        self.assertEqual(metadata["recipe"], str(self.recipe))
        self.assertEqual(metadata["arguments"], ["--", "show", "vm-demo-uri"])

    def test_invalid_explicit_override_fails_without_default_recipe_fallback(self):
        result = subprocess.run([str(self.source / "nixberry"), "list"],
                                env={**self.environment, "NIXBERRY_RECIPE": str(self.root / "missing")},
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("incomplete or unavailable", result.stderr)

    def test_export_plans_reference_the_same_recipe_for_every_platform_and_method(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware", "rpi"):
            for method in (("image",) if backend == "rpi" else ("image", "iso")):
                with self.subTest(backend=backend, method=method):
                    plan = export(self.recipe, "vm-demo-uri", {"backend": backend, "spec": {}},
                                  self.root / "bundle", dry_run=True, method=method)
                    self.assert_declaration(self.metadata(plan["build"][-1]))

    def test_encrypted_image_plan_preserves_recipe_path_on_every_vm_backend(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            with self.subTest(backend=backend):
                target = {"backend": backend, "security": {"mode": "luks"}, "spec": {}}
                plan = prepare_image(self.recipe, "vm-demo-uri", target, self.root / "encrypted", dry_run=True)
                self.assert_declaration(self.metadata(plan["build"][-1]))
