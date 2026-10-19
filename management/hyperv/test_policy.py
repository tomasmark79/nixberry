"""Jiná deklarovaná Hyper-V VM je povolená; nebezpečné názvy a cesty nejsou."""

import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from policy import validate

SPEC = {
    "name": "nixberry-another", "ownerId": "nixberry-hyperv-85a5abbf-ac9f-44ad-9f34-34dd14b77123",
    "windowsComputerName": "OTHER", "generation": 2, "secureBoot": False,
    "mac": "027981000055", "storageDirectory": r"C:\ProgramData\nixberry\hyperv",
    "diskGiB": 48, "memoryMiB": 4096, "vcpus": 2,
    "switchId": "c08cb7b8-9b3c-408e-8e30-5e16a3aeb444",
}


class PolicyChecks(unittest.TestCase):
    def test_another_named_target_is_allowed(self):
        validate(SPEC)

    def test_unsafe_identity_or_disk_is_rejected(self):
        for key, value in (("name", "foreign"), ("name", "nixberry-../other"),
                           ("ownerId", "foreign"), ("secureBoot", True), ("generation", 1),
                           ("mac", "not-a-mac"), ("storageDirectory", r"C:\data\..\other"),
                           ("storageDirectory", r"\\server\share"), ("vcpus", 0),
                           ("diskGiB", 0), ("memoryMiB", True), ("switchId", "invalid")):
            with self.subTest(key=key, value=value), self.assertRaises((RuntimeError, ValueError)):
                validate({**SPEC, key: value})

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell není k dispozici")
    def test_windows_name_and_owner_guards_without_hypervisor_access(self):
        # Spouštíme jen validační úvod, před importem Hyper-V a před jakýmkoli zápisem.
        guard = Path(__file__).with_name("host.ps1").read_text().split("Import-Module Hyper-V")[0]
        for fields, allowed in (({}, True), ({"name": "nixberry-second-hv"}, True),
                                ({"name": "../other"}, False), ({"ownerId": "foreign"}, False)):
            spec = {**SPEC, **fields}
            encoded = base64.b64encode(json.dumps({"spec": spec}).encode()).decode()
            script = ("$env:COMPUTERNAME='OTHER'; $Request=[Text.Encoding]::UTF8.GetString("
                      "[Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json;\n" + guard)
            result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                    capture_output=True, text=True, timeout=15)
            with self.subTest(fields=fields):
                self.assertEqual(result.returncode == 0, allowed, result.stderr)


if __name__ == "__main__":
    unittest.main()
