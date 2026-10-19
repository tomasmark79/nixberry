"""Místní offline obnova sdílí ochrany SSD a bez --apply nikdy nespustí instalaci."""

from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from test_install import API, SNAPSHOT, TARGET

MAIN = runpy.run_path(str(Path(__file__).with_name("local-install.py")))["main"]


class LocalInstallChecks(unittest.TestCase):
    def invoke(self, args, snapshot, run):
        target = {**TARGET, "diskDevice": "/dev/disk/by-id/test"}
        api = {**API, "parse_probe": lambda _: snapshot}
        env = {"NIXBERRY_RPI_MODULE": "module", "NIXBERRY_RPI_PROBE": "probe",
               "NIXBERRY_RPI_GUEST": "guest", "NIXBERRY_RPI_JQ": "/nix/store/native-jq"}
        with patch.dict(os.environ, env), patch("runpy.run_path", return_value=api), \
                patch("pathlib.Path.read_text", return_value=json.dumps({"test": target})), \
                patch("subprocess.check_output", return_value="probe"), \
                patch("subprocess.run", run), contextlib.redirect_stdout(io.StringIO()):
            return MAIN(["--target", "test", *args])

    def test_default_only_checks_offline_store(self):
        from unittest.mock import Mock
        run = Mock()
        self.invoke([], SNAPSHOT, run)
        self.assertEqual(run.call_count, 1)
        self.assertIn("--offline", run.call_args.args[0])

    def test_apply_invokes_existing_guest_guard(self):
        from unittest.mock import Mock
        run = Mock()
        self.invoke(["--apply"], SNAPSHOT, run)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][:3], ["bash", "-s", "--"])
        self.assertIn("input", run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][-1], "clean-only")

    def test_mounted_ssd_cannot_be_erased(self):
        from unittest.mock import Mock
        snapshot = deepcopy(SNAPSHOT)
        snapshot["disks"][0]["mountpoints"] = ["/"]
        run = Mock()
        with self.assertRaises(RuntimeError):
            self.invoke(["--apply", "--erase-existing"], snapshot, run)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
