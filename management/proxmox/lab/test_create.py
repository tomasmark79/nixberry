"""Ochrana existujících prostředků a chybového návratu instalačního asistenta."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pvelab_create", Path(__file__).with_name("create.py"))
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class LabChecks(unittest.TestCase):
    def test_assistant_error_is_failure_even_with_zero_exit_status(self):
        result = subprocess.CompletedProcess([], 0, "Checking answer file", "Error: invalid answer")
        with patch.object(lab.subprocess, "run", return_value=result), self.assertRaisesRegex(RuntimeError, "invalid answer"):
            lab.run(["proxmox-auto-install-assistant", "validate-answer", "/answer"])

    def test_existing_vm_rejected_before_network_storage_and_download(self):
        with patch.object(lab, "run", return_value="nixon"), \
                patch.object(Path, "read_text", return_value="1"), \
                patch.object(lab, "virsh", return_value=lab.NAME) as virsh, \
                patch.object(lab.urllib.request, "urlopen") as download, \
                self.assertRaisesRegex(RuntimeError, "už existuje"):
            lab.preflight()
        self.assertEqual(virsh.call_count, 1)
        download.assert_not_called()

    def test_corrupt_iso_rejected_before_credentials_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / lab.ISO_NAME).write_bytes(b"wrong image")
            with patch.object(lab, "ROOT", root), patch.object(lab, "STATE", root / "state"), \
                    self.assertRaisesRegex(RuntimeError, "součet"):
                lab.prepare_iso()
            self.assertFalse((root / "state").exists())


if __name__ == "__main__":
    unittest.main()
