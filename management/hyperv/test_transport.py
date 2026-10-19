"""Windows skript má jasný konec vstupu a vejde se do limitu cmd.exe."""

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

API = runpy.run_path(str(Path(__file__).with_name("probe.py")))


class TransportChecks(unittest.TestCase):
    def test_input_has_exact_end_marker(self):
        text = API["powershell_input"]("Write-Output 'Příliš žluťoučký kůň'\n")
        self.assertEqual(text.splitlines()[-1], API["INPUT_END"])
        self.assertIn("žluťoučký", text)

    def test_marker_inside_script_is_rejected(self):
        with self.assertRaises(ValueError):
            API["powershell_input"]("Write-Output 'before'\n" + API["INPUT_END"] + "\n")

    def test_bootstrap_does_not_wait_for_eof(self):
        command = API["powershell_command"]()
        self.assertLess(len(command), 8191)
        script = base64.b64decode(command.split()[-1]).decode("utf-16le")
        self.assertIn("ReadLine", script)
        self.assertIn(API["INPUT_END"], script)
        self.assertNotIn("ReadToEnd", script)

    def test_status_never_loads_full_inventory_and_passes_names_as_data(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = Path(directory) / "spec.json"
            spec.write_text(json.dumps({"sshHost": "192.168.50.10", "sshUser": "operator", "name": "default"}))
            env = {"NIXBERRY_HYPERV_SPEC": str(spec),
                   "NIXBERRY_HYPERV_STATUS": str(Path(__file__).with_name("status.ps1")),
                   "NIXBERRY_HYPERV_PROBE": str(Path(directory) / "unavailable-full-probe.ps1")}
            argv = ["probe.py", "--status-only", "--vm-name", "vm-one", "--vm-name", "vm-two"]
            with patch.dict(os.environ, env), patch.object(sys, "argv", argv), \
                    patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"vms":[]}', stderr="")) as run, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                API["main"]()
            self.assertEqual(json.loads(output.getvalue()), {"vms": []})
            source = run.call_args.kwargs["input"]
            payload = source.split("FromBase64String('", 1)[1].split("'", 1)[0]
            self.assertEqual(json.loads(base64.b64decode(payload)), ["vm-one", "vm-two"])
            self.assertNotIn("Get-VHD", source)
            self.assertNotIn("Get-VMSnapshot", source)
            self.assertEqual(run.call_args.kwargs["timeout"], 20)


if __name__ == "__main__":
    unittest.main()
