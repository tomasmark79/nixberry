"""Linked configuration sources must survive immutable capture and export."""

from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

from recipe_source import materialized_recipe


class RecipeSourceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.recipe = self.root / "recipe"
        (self.recipe / "configurations").mkdir(parents=True)
        (self.recipe / "flake.nix").write_text("{}")
        self.external = self.root / "serverconf"
        self.external.mkdir()
        (self.external / "default.nix").write_text("{ services.openssh.enable = true; }")

    def link(self):
        path = self.recipe / "configurations/web"
        path.symlink_to("../../serverconf", target_is_directory=True)
        return path

    def test_unlinked_recipe_keeps_original_path(self):
        with materialized_recipe(self.recipe) as source:
            self.assertEqual(Path(source), self.recipe)

    def test_capture_is_independent_of_link_source_and_excludes_git_metadata(self):
        self.link()
        (self.external / ".git").mkdir()
        (self.external / ".git/config").write_text("private metadata")
        (self.external / "result").symlink_to(self.root, target_is_directory=True)
        with materialized_recipe(self.recipe) as source:
            captured = Path(source) / "configurations/web"
            self.assertTrue(captured.is_dir())
            self.assertFalse(captured.is_symlink())
            self.assertFalse((captured / ".git").exists())
            self.assertFalse((captured / "result").exists())
            original = (captured / "default.nix").read_text()
            (self.external / "default.nix").write_text("changed")
            self.assertEqual((captured / "default.nix").read_text(), original)
            self.assertTrue((self.recipe / "configurations/web").is_symlink())
        self.assertFalse(Path(source).exists())

    def test_broken_or_nonmodule_directory_link_fails(self):
        link = self.link()
        (self.external / "default.nix").unlink()
        for state in ("empty", "broken"):
            with self.subTest(state=state):
                if state == "broken":
                    self.external.rmdir()
                with self.assertRaisesRegex(RuntimeError, "regular default.nix"):
                    with materialized_recipe(self.recipe):
                        self.fail("Invalid linked source was accepted")
        self.assertTrue(link.is_symlink())

    def test_nested_symlink_is_not_followed_into_unrelated_files(self):
        self.link()
        (self.external / "unrelated").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "contains a symbolic link"):
            with materialized_recipe(self.recipe):
                self.fail("Nested symlink was accepted")

    def test_default_module_symlink_is_rejected(self):
        self.link()
        (self.external / "default.nix").unlink()
        (self.external / "default.nix").symlink_to(self.recipe / "flake.nix")
        with self.assertRaisesRegex(RuntimeError, "regular default.nix"):
            with materialized_recipe(self.recipe):
                self.fail("Linked default.nix was accepted")

    @unittest.skipUnless(shutil.which("nix"), "Nix is required")
    def test_frozen_recipe_survives_external_edits_without_git_metadata(self):
        from updater import freeze_recipe
        self.link()
        (self.external / "default.nix").write_text('"captured"')
        (self.external / ".git").mkdir()
        (self.external / ".git/config").write_text("private metadata")
        (self.recipe / "flake.nix").write_text('''{
          outputs = { self }: {
            nixberryDeployment.targets.test = { backend = "libvirt"; spec = {}; };
            value = import ./configurations/web;
          };
        }''')
        (self.recipe / "flake.lock").write_text(json.dumps({
            "version": 7, "root": "root", "nodes": {"root": {}}}))
        with tempfile.TemporaryDirectory() as roots:
            frozen = freeze_recipe(str(self.recipe), "test", {"backend": "libvirt", "spec": {}}, roots)
            (self.external / "default.nix").write_text('"changed"')
            value = subprocess.check_output([
                "nix", "eval", "--json", "--no-write-lock-file", "path:" + frozen + "#value"], text=True)
            self.assertEqual(json.loads(value), "captured")
            captured = Path(frozen) / "configurations/web"
            self.assertFalse(captured.is_symlink())
            self.assertFalse((captured / ".git").exists())
