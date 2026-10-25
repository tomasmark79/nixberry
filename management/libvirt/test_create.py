#!/usr/bin/env python3
"""Ověření ochranných kontrol spouštěče bez zápisu do skutečného libvirt."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("create.sh")
MOCK_VIRSH = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[3:]
command = args[0]
with open(os.environ["CALL_LOG"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")
case = os.environ.get("MOCK_CASE", "")
if command == "list":
    if case == "connection-error":
        sys.exit(1)
    if "--uuid" in args:
        print("")
    elif "--name" in args:
        print("nixberry-repro-test" if case == "existing-domain" else ("peer" if case == "secondary-mac-used" else ""))
elif command == "pool-info":
    print("State: running")
    if case == "verbose-info":
        print("details " * 100000)
elif command == "pool-dumpxml":
    path = "/wrong-pool" if case == "wrong-pool" else os.environ["POOL_PATH"]
    print("<pool><target><path>" + path + "</path></target></pool>")
elif command == "vol-list":
    print(" Name Path")
    print("-----------")
    if case == "existing-volume":
        print(" nixberry-repro-test.qcow2 /some/path")
elif command == "net-info":
    print("Active: no" if case == "secondary-inactive" and args[1] == "vm-demo-lan" else "Active: yes")
    if case == "verbose-info":
        print("details " * 100000)
elif command == "net-dumpxml":
    mode = "bridge" if case == "wrong-network" else "nat"
    if case == "secondary-isolated" and args[1] == "vm-demo-lan":
        print("<network/>")
    elif case == "secondary-reservation" and args[1] == "vm-demo-lan":
        print("<network><forward mode='nat'/><ip><dhcp><host mac='52:54:00:79:81:02'/></dhcp></ip></network>")
    else:
        print("<network><forward mode='" + mode + "'/></network>")
elif command == "dumpxml":
    print("<domain><devices><interface><mac address='52:54:00:79:81:02'/></interface></devices></domain>")
elif command == "domcapabilities":
    print("<domainCapabilities/>")
elif command in ("vol-create-as", "vol-upload", "define", "start"):
    pass
elif command == "vol-path":
    print(os.environ["DISK_PATH"])
else:
    print("Unexpected command " + command, file=sys.stderr)
    sys.exit(2)
"""


class CreationChecks(unittest.TestCase):
    def test_secondary_network_and_mac_guards_run_before_writes(self):
        manifest = json.loads(self.manifest.read_text())
        manifest['networkAdapters'] = [
            {'name': 'wan0', 'mac': manifest['mac'], 'network': manifest['network']},
            {'name': 'lan0', 'mac': '52:54:00:79:81:02', 'network': 'vm-demo-lan'}]
        self.manifest.write_text(json.dumps(manifest))
        for case, allowed in [('secondary-isolated', True), ('secondary-inactive', False),
                              ('secondary-mac-used', False), ('secondary-reservation', False)]:
            with self.subTest(case=case):
                self.log.unlink(missing_ok=True)
                result = self.run_command('--dry-run', case=case)
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertFalse(any(call[0] in ('vol-create-as', 'vol-upload', 'define', 'start') for call in calls))

    def setUp(self):
        for command in ("jq", "xmllint"):
            if shutil.which(command) is None:
                self.skipTest(f"Chybí {command}")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tools = self.root / "bin"
        tools.mkdir()
        for name, content in {
            "virsh": MOCK_VIRSH,
            "virt-xml-validate": "#!/bin/sh\nexit 0\n",
        }.items():
            path = tools / name
            path.write_text(content)
            path.chmod(0o755)
        self.iso = self.root / "installer.iso"
        self.iso.touch()
        self.disk = self.root / "nixberry-repro-test.qcow2"
        self.log = self.root / "calls.jsonl"
        manifest = {
            "deploymentMethod": "iso",
            "uri": "qemu:///system",
            "name": "nixberry-repro-test",
            "uuid": "315b9a43-1653-4b2f-9cd9-790081000001",
            "storagePool": "default",
            "storageDirectory": str(self.root),
            "volume": self.disk.name,
            "diskPath": str(self.disk),
            "diskGiB": 32,
            "network": "default",
            "mac": "52:54:00:79:81:01",
            "isoPath": str(self.iso),
            "machine": "pc-q35-9.2",
        }
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps(manifest))
        self.env = os.environ | {
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "NIXBERRY_MANIFEST": str(self.manifest),
            "NIXBERRY_DOMAIN_XML": str(self.root / "domain.xml"),
            "CALL_LOG": str(self.log),
            "POOL_PATH": str(self.root),
            "DISK_PATH": str(self.disk),
        }

    def run_command(self, *args, case=""):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env | {"MOCK_CASE": case},
            capture_output=True,
            text=True,
            check=False,
        )

    def mutations(self):
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [call[0] for call in calls if call[0] in {"vol-create-as", "vol-upload", "define", "start"}]

    def test_default_is_read_only(self):
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])

    def test_apply_sequence_against_mock(self):
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), ["vol-create-as", "define", "start"])

    def test_image_import_does_not_start_installer_or_vm_and_dry_run_never_writes(self):
        manifest = json.loads(self.manifest.read_text())
        image = self.root / "system.qcow2"
        image.write_bytes(b"prepared-disk")
        manifest.update(deploymentMethod="image", isoPath=None, imagePath=str(image))
        self.manifest.write_text(json.dumps(manifest))
        result = self.run_command("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), ["vol-create-as", "vol-upload", "define"])

    def test_verbose_info_does_not_trigger_sigpipe(self):
        result = self.run_command(case="verbose-info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])

    def test_preflight_failures_never_write(self):
        for case in ("existing-domain", "existing-volume", "wrong-pool", "wrong-network", "connection-error"):
            with self.subTest(case=case):
                self.log.unlink(missing_ok=True)
                result = self.run_command("--apply", case=case)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])

    def test_existing_file_is_preserved(self):
        self.disk.write_bytes(b"existing data")
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.disk.read_bytes(), b"existing data")
        self.assertEqual(self.mutations(), [])

    def test_existing_symlink_is_preserved(self):
        self.disk.symlink_to(self.root / "missing-target")
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.disk.is_symlink())
        self.assertEqual(self.mutations(), [])

    def test_remote_uri_is_rejected(self):
        data = json.loads(self.manifest.read_text())
        data["uri"] = "qemu+ssh://192.168.79.1/system"
        self.manifest.write_text(json.dumps(data))
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_extra_argument_is_rejected(self):
        result = self.run_command("--apply", "--unexpected")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
