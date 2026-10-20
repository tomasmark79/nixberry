"""Compare unsigned, absent store paths using real nvd and cached metadata."""

import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import closure_metadata as metadata


OLD = "/nix/store/" + "a" * 32 + "-nixos-system-old-26.05"
NEW = "/nix/store/" + "b" * 32 + "-nixos-system-new-26.05"
BEFORE = "/nix/store/" + "c" * 32 + "-hello-1.0"
AFTER = "/nix/store/" + "d" * 32 + "-hello-2.0"


class MetadataChecks(unittest.TestCase):
    def test_reads_sizes_without_imports_and_preserves_ssh_options(self):
        output = OLD + " 20\n" + BEFORE + " 40\n\0" + BEFORE + "\n"
        ssh = ["ssh", "-o", "StrictHostKeyChecking=yes", "-J", "jump", "--", "root@guest"]
        with patch.object(metadata.subprocess, "check_output", return_value=output) as read:
            result = metadata.read_snapshot(OLD, ssh)
        self.assertEqual(result["size"], 60)
        self.assertEqual(result["selected"], [BEFORE])
        command = read.call_args.args[0]
        self.assertEqual(command[:-1], ssh)
        self.assertNotIn("copy", command[-1])
        self.assertNotIn("no-check-sigs", command[-1])
        self.assertNotIn("add-root", command[-1])
        self.assertEqual(read.call_args.kwargs, {"text": True, "timeout": 120})

    def test_local_read_accepts_nix_column_spacing(self):
        output = OLD + "\t   42\n\0" + OLD + "\n"
        with patch.object(metadata.subprocess, "check_output", return_value=output) as read:
            self.assertEqual(metadata.read_snapshot(OLD)["size"], 42)
        self.assertEqual(read.call_args.args[0][:2], ["sh", "-c"])

    def test_invalid_or_incomplete_metadata_fails(self):
        for output in ("missing separator", "\0", OLD + " -1\0", OLD + " 1\0" + BEFORE,
                       OLD + " NaN\0", OLD + " 1\nbad-path 2\0", OLD + " 1\n" + OLD + " 2\0"):
            with self.subTest(output=output), \
                    patch.object(metadata.subprocess, "check_output", return_value=output):
                with self.assertRaisesRegex(RuntimeError, "Invalid closure metadata"):
                    metadata.read_snapshot(OLD)

    def test_unknown_queries_cannot_fall_back_to_real_nix(self):
        with self.assertRaisesRegex(ValueError, "Unsupported nvd metadata query"):
            metadata.query({}, "nix", ["copy", "--no-check-sigs"])

    @unittest.skipUnless(shutil.which("nvd") and shutil.which("nix-build"), "nvd and Nix required")
    def test_real_nvd_compares_absent_unsigned_paths_and_closure_sizes(self):
        previous = {"system": OLD, "closure": [OLD, BEFORE], "selected": [BEFORE], "size": 1000}
        expected = {"system": NEW, "closure": [NEW, AFTER], "selected": [AFTER], "size": 2000}
        with tempfile.TemporaryDirectory() as directory:
            command = metadata.comparison_command(directory, previous, expected)
            result = subprocess.run(command, text=True, capture_output=True, timeout=20,
                                    env={"PATH": "/no-external-programs", "NO_COLOR": "1"},
                                    executable=shutil.which("nvd"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hello", result.stdout)
        self.assertIn("1.0", result.stdout)
        self.assertIn("2.0", result.stdout)
        self.assertIn("Closure size", result.stdout)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
