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
        for name in ("a", "vm-demo-hyperv", "a" * 63):
            with self.subTest(name=name):
                validate({**SPEC, "name": name})

    def test_unsafe_identity_or_disk_is_rejected(self):
        for key, value in (("name", "../foreign"), ("name", "nixberry-../other"),
                           ("name", ""), ("name", "Bad"), ("name", "a" * 64), ("name", "a\n"),
                           ("name", "con"), ("name", "nul"), ("name", "com1"), ("name", "lpt9"),
                           ("ownerId", "foreign"), ("secureBoot", True), ("generation", 1),
                           ("mac", "not-a-mac"), ("storageDirectory", r"C:\data\..\other"),
                           ("storageDirectory", r"\\server\share"), ("vcpus", 0),
                           ("diskGiB", 0), ("memoryMiB", True), ("switchId", "invalid")):
            with self.subTest(key=key, value=value), self.assertRaises((RuntimeError, ValueError)):
                validate({**SPEC, key: value})

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell není k dispozici")
    def test_windows_name_and_owner_guards_without_hypervisor_access(self):
        # Run only validation, before importing Hyper-V or performing writes.
        guards = {name: Path(__file__).with_name(name).read_text().split("Import-Module Hyper-V")[0]
                  for name in ("host.ps1", "reset.ps1")}
        for fields, allowed in (({}, True), ({"name": "nixberry-second-hv"}, True),
                                ({"name": "vm-demo-hyperv"}, True), ({"name": "a" * 63}, True),
                                ({"name": "a" * 64}, False), ({"name": "a\n"}, False),
                                ({"name": "con"}, False), ({"name": "nul"}, False),
                                ({"name": "com1"}, False), ({"name": "lpt9"}, False),
                                ({"name": "../other"}, False), ({"ownerId": "foreign"}, False)):
            spec = {**SPEC, "uuid": "85a5abbf-ac9f-44ad-9f34-34dd14b77123", **fields}
            encoded = base64.b64encode(json.dumps({"spec": spec, "allowReset": True, "apply": False}).encode()).decode()
            for filename, guard in guards.items():
                script = ("$env:COMPUTERNAME='OTHER'; $Request=[Text.Encoding]::UTF8.GetString("
                          "[Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json;\n" + guard)
                result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                        capture_output=True, text=True, timeout=15)
                with self.subTest(script=filename, fields=fields):
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)


if __name__ == "__main__":
    unittest.main()
