"""Porovnání nesmí aktivovat server ani přenášet systém směrem na něj."""

import contextlib
import io
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import diffing


OLD = "/nix/store/" + "a" * 32 + "-nixos-system-old-26.05"
NEW = "/nix/store/" + "b" * 32 + "-nixos-system-new-26.05"
TARGET = {"backend": "hyperv", "host": "windows", "spec": {}}
SSH = ["ssh", "-o", "StrictHostKeyChecking=yes", "-J", "operator@windows", "--", "root@172.21.222.76"]


class DiffChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.recipe = directory.name
        for name in ("flake.nix", "flake.lock"):
            Path(self.recipe, name).touch()
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.messages = io.StringIO()
        redirect = contextlib.redirect_stderr(self.messages)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.dependencies = {}
        for name, value in (("freeze_recipe", "/nix/store/frozen-recipe"),
                            ("status", {"state": "running", "addresses": ["172.21.222.76"]}),
                            ("preflight", OLD), ("build", NEW), ("connection", SSH)):
            patcher = patch.object(diffing, name, return_value=value)
            self.dependencies[name] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(diffing.subprocess, "run", return_value=Mock(returncode=0))
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_plans_do_not_connect_or_build(self):
        for generations in (False, True):
            self.assertEqual(diffing.compare(self.recipe, "test", TARGET,
                                            generations=generations, dry_run=True), 0)
        self.run.assert_not_called()
        for dependency in self.dependencies.values():
            dependency.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")
        self.assertIn("[nixberry] Plan:", self.messages.getvalue())

    def test_recipe_comparison_only_copies_from_server_and_keeps_jump_host(self):
        self.assertEqual(diffing.compare(self.recipe, "test", TARGET), 0)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[0], ["nix", "copy", "--no-check-sigs", "--from", "ssh://root@172.21.222.76", OLD])
        options = shlex.split(self.run.call_args_list[0].kwargs["env"]["NIX_SSHOPTS"])
        self.assertEqual(options, SSH[1:SSH.index("--")])
        self.assertEqual(commands[-1], ["nvd", "diff", OLD, NEW])
        self.assertIn("--add-root", commands[1])
        self.assertNotIn("--to", sum(commands, []))
        self.assertEqual(len(commands), 3)
        self.assertEqual(self.dependencies["build"].call_args.args[0], "/nix/store/frozen-recipe")

    def test_unsigned_copy_is_scoped_to_verified_source_on_every_platform(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware", "rpi"):
            with self.subTest(backend=backend):
                self.run.reset_mock()
                self.assertEqual(diffing.compare(self.recipe, "test", {**TARGET, "backend": backend}), 0)
                commands = [call.args[0] for call in self.run.call_args_list]
                self.assertEqual(commands[0], ["nix", "copy", "--no-check-sigs", "--from",
                                               "ssh://root@172.21.222.76", OLD])
                self.assertEqual(shlex.split(self.run.call_args_list[0].kwargs["env"]["NIX_SSHOPTS"]),
                                 SSH[1:SSH.index("--")])
                self.assertEqual(sum("--no-check-sigs" in command for command in commands), 1)
                self.assertEqual(commands[-1], ["nvd", "diff", OLD, NEW])

    def test_identity_failure_never_reaches_unsigned_copy(self):
        self.dependencies["preflight"].side_effect = [OLD, RuntimeError("Identity mismatch")]
        with self.assertRaisesRegex(RuntimeError, "Identity mismatch"):
            diffing.compare(self.recipe, "test", TARGET)
        self.run.assert_not_called()

    def test_stale_cli_stops_before_connecting(self):
        self.dependencies["freeze_recipe"].side_effect = RuntimeError("The target declaration differs")
        with self.assertRaisesRegex(RuntimeError, "The target declaration differs"):
            diffing.compare(self.recipe, "test", TARGET)
        self.dependencies["status"].assert_not_called()
        self.dependencies["build"].assert_not_called()
        self.run.assert_not_called()

    def test_generation_comparison_is_remote_without_build_or_copy(self):
        diffing.compare("", "test", TARGET, generations=True)
        self.run.assert_called_once_with(SSH + [diffing.generation_script("test")], check=False)
        self.dependencies["build"].assert_not_called()
        self.dependencies["freeze_recipe"].assert_not_called()

    def test_remote_colors_follow_local_terminal_and_no_color(self):
        for terminal, environment, colored in (
            (True, {"TERM": "xterm-256color"}, True),
            (False, {"TERM": "xterm-256color"}, False),
            (True, {"NO_COLOR": "", "TERM": "xterm"}, False),
            (True, {"TERM": "dumb"}, False),
        ):
            with self.subTest(terminal=terminal, environment=environment):
                with patch.object(diffing.sys.stdout, "isatty", return_value=terminal), \
                        patch.dict(os.environ, environment, clear=True):
                    diffing.compare("", "test", TARGET, generations=True)
                script = self.run.call_args.args[0][-1]
                self.assertEqual(script.startswith("NIXBERRY_DIFF_COLOR=always\n"), colored)

    def test_change_during_build_stops_before_copy(self):
        self.dependencies["preflight"].side_effect = [OLD, NEW]
        with self.assertRaisesRegex(RuntimeError, "during the build"):
            diffing.compare(self.recipe, "test", TARGET)
        self.run.assert_not_called()

    def test_change_during_copy_stops_before_diff(self):
        self.dependencies["preflight"].side_effect = [OLD, OLD, NEW]
        with self.assertRaisesRegex(RuntimeError, "during transfer"):
            diffing.compare(self.recipe, "test", TARGET)
        self.assertEqual(len(self.run.call_args_list), 2)

    def test_invalid_recipe_and_name_do_not_connect(self):
        for recipe, name in (("", "test"), (self.recipe, "test;reboot")):
            with self.assertRaises(RuntimeError):
                diffing.compare(recipe, name, TARGET)
        self.dependencies["status"].assert_not_called()

    def test_copy_failure_never_runs_diff(self):
        self.run.side_effect = subprocess.CalledProcessError(1, "nix copy")
        with self.assertRaises(subprocess.CalledProcessError):
            diffing.compare(self.recipe, "test", TARGET)
        self.assertEqual(self.run.call_count, 1)


class GenerationScriptChecks(unittest.TestCase):
    def setUp(self):
        # Generovaný příkaz v těchto zkouškách představuje přesměrovaný stderr.
        redirect = contextlib.redirect_stderr(io.StringIO())
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def test_translated_headers_preserve_percent_backslash_and_shell_characters(self):
        def translate(message):
            return "Info 50% \\n '{path}' $(exit 99)" if "{path}" in message else message

        with tempfile.TemporaryDirectory() as directory:
            for number in (1, 2):
                (Path(directory) / f"system-{number}-link").symlink_to("/missing-system")
            with patch.object(diffing, "_", side_effect=translate):
                script = diffing.generation_script("test")
            script = script.replace("/nix/var/nix/profiles", shlex.quote(directory))
            script = script.replace("/run/current-system/sw/bin/nvd", "true")
            result = subprocess.run(["sh", "-c", script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("50% \\n '", result.stderr)
        self.assertIn("$(exit 99)", result.stderr)

    def test_numeric_order_missing_generations_and_remote_exit_status(self):
        with tempfile.TemporaryDirectory(prefix="nixberry-generations-") as directory:
            root = Path(directory)
            script = diffing.generation_script("test").replace("/nix/var/nix/profiles", shlex.quote(directory))
            # Nahrazujeme pouze nvd, skutečné find/sort/readlink a shell zůstávají.
            script = script.replace("/run/current-system/sw/bin/nvd", 'sh -c \'printf "%s\\n" "$@"; exit 23\' nvd')
            for numbers in ((), (2,), (2, 9, 10)):
                for number in numbers:
                    link = root / f"system-{number}-link"
                    if not link.is_symlink():
                        link.symlink_to("/missing-system")
                result = subprocess.run(["sh", "-c", script], text=True, capture_output=True)
                if len(numbers) < 2:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("two retained", result.stderr)
                else:
                    self.assertEqual(result.returncode, 23)
                    self.assertIn("[nixberry] Previous generation:", result.stderr)
                    self.assertNotIn("[nixberry]", result.stdout)
                    self.assertEqual(result.stdout.splitlines()[-3:],
                                     ["diff", str(root / "system-9-link"), str(root / "system-10-link")])


if __name__ == "__main__":
    unittest.main()
