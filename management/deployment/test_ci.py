"""The CI recipe contains public tracked sources and an isolated test identity."""

from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from check_ci import TEST_KEY, public_recipe


class PublicRecipeChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.destination = self.root / "recipe"
        self.source.mkdir()
        self.git("init", "--quiet")

    def git(self, *arguments):
        subprocess.run(["git", *arguments], cwd=self.source, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def track(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.git("add", "--", name)
        return path

    def test_exports_current_tracked_contents_without_local_data(self):
        self.track(".gitignore", "admin.pub\nhosts.local.nix\ninstances/*.nix\n"
                   "!instances/default.nix\nmanagement/rpi/targets/*.nix\n"
                   "!management/rpi/targets/default.nix\n")
        module = self.track("configurations/default.nix", '"staged version"\n')
        module.write_text('"current version"\n')
        self.track("instances/default.nix", "{}\n")
        self.track("management/rpi/targets/default.nix", "{}\n")
        local_files = {
            "admin.pub": "private-local-identity\n",
            "hosts.local.nix": "private-local-connections\n",
            "instances/local.nix": "private-local-vm\n",
            "management/rpi/targets/local.nix": "private-local-rpi\n",
            "untracked.txt": "untracked-local-data\n",
        }
        for name, content in local_files.items():
            (self.source / name).write_text(content)

        files = public_recipe(self.source, self.destination)

        self.assertEqual((self.destination / "configurations/default.nix").read_text(),
                         '"current version"\n')
        self.assertEqual((self.destination / "admin.pub").read_text(), TEST_KEY)
        self.assertTrue((self.destination / "instances/default.nix").is_file())
        self.assertTrue((self.destination / "management/rpi/targets/default.nix").is_file())
        self.assertFalse((self.destination / ".git").exists())
        for name, content in local_files.items():
            with self.subTest(path=name):
                self.assertNotIn(Path(name), files)
                self.assertEqual((self.source / name).read_text(), content)
                if name != "admin.pub":
                    self.assertFalse((self.destination / name).exists())

    def test_preserves_executable_mode_and_relative_internal_links(self):
        script = self.track("bin/run", "#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        link = self.source / "bin/current"
        link.symlink_to("run")
        self.git("add", "--", "bin/current")

        public_recipe(self.source, self.destination)

        copied = self.destination / "bin/current"
        self.assertTrue(copied.is_symlink())
        self.assertEqual(copied.readlink(), Path("run"))
        self.assertEqual(stat.S_IMODE((self.destination / "bin/run").stat().st_mode), 0o755)
        script.write_text("changed after export\n")
        self.assertEqual(copied.read_text(), "#!/bin/sh\nexit 0\n")

    def test_rejects_absolute_internal_link_to_original_local_identity(self):
        self.track(".gitignore", "admin.pub\n")
        identity = self.source / "admin.pub"
        identity.write_text("private-local-identity\n")
        link = self.source / "key-alias"
        link.symlink_to(identity)
        self.git("add", "--", "key-alias")

        with self.assertRaisesRegex(RuntimeError, "escapes the isolated recipe"):
            public_recipe(self.source, self.destination)

        self.assertEqual(link.read_text(), "private-local-identity\n")

    def test_rejects_tracked_external_symbolic_link(self):
        external = self.root / "private.nix"
        external.write_text("private-external-data\n")
        (self.source / "external.nix").symlink_to(external)
        self.git("add", "--", "external.nix")

        with self.assertRaisesRegex(RuntimeError, "escapes the isolated recipe"):
            public_recipe(self.source, self.destination)

        self.assertEqual(external.read_text(), "private-external-data\n")

    def test_rejects_tracked_file_below_replaced_external_directory(self):
        tracked = self.track("configurations/server/default.nix", "public module\n")
        external = self.root / "private-configuration"
        external.mkdir()
        (external / "default.nix").write_text("private-external-data\n")
        tracked.unlink()
        tracked.parent.rmdir()
        tracked.parent.symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "escapes the isolated recipe"):
            public_recipe(self.source, self.destination)

        self.assertEqual((external / "default.nix").read_text(), "private-external-data\n")

    def test_rejects_tracked_file_below_replaced_internal_directory(self):
        tracked = self.track("configurations/server/default.nix", "public module\n")
        ignored = self.source / "ignored"
        ignored.mkdir()
        (ignored / "default.nix").write_text("private-internal-data\n")
        tracked.unlink()
        tracked.parent.rmdir()
        tracked.parent.symlink_to("../../ignored", target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "escapes the isolated recipe"):
            public_recipe(self.source, self.destination)

        self.assertEqual((ignored / "default.nix").read_text(), "private-internal-data\n")


if __name__ == "__main__":
    unittest.main()
