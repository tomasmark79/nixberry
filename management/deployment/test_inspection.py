"""Diagnostika nesmí přeskakovat chyby ani zaměnit rollback za odpad."""

import json
import subprocess
import unittest
from unittest.mock import patch

import inspection
import cleanup


class InspectionTests(unittest.TestCase):
    def test_direct_guest_agent_is_optional_but_network_is_required(self):
        def fixture(command, probes):
            result = {key: {"code": 0, "output": ""} for key in probes}
            for key, value in {"hostname": "guest", "architecture": "x86_64", "routes": "[]",
                               "interfaces": "[]", "services": "active\ninactive\ninactive"}.items():
                result[key]["output"] = value
            return result
        target = {"backend": "proxmox", "offline": True, "guestHostName": "guest", "spec": {},
                  "_guestConnection": {"host": "203.0.113.19", "user": "root", "port": 2030}}
        with patch.object(inspection, "status", return_value={"state": "running", "addresses": ["203.0.113.19"]}), \
                patch.object(inspection, "shell_probe", side_effect=fixture):
            report = inspection.doctor(target)
        checks = {check["name"]: check for check in report["checks"]}
        self.assertEqual(checks["service:qemu-guest-agent"]["level"], "warning")
        self.assertEqual(checks["service:systemd-networkd"]["level"], "error")

    def test_probe_runs_real_shell_and_retains_independent_failures(self):
        def local(command):
            return subprocess.check_output(["bash", "-c", command[-1]], text=True).strip()
        with patch.object(inspection, "read", side_effect=local):
            result = inspection.shell_probe([], {"broken": "echo problem; exit 7",
                                                "good": "printf 'a\\nb'", "empty": "true"})
        self.assertEqual(result["broken"], {"code": 7, "output": "problem"})
        self.assertEqual(result["good"]["output"], "a\nb")
        self.assertEqual(result["empty"]["output"], "")

    def test_missing_duplicate_and_invalid_base64_are_rejected(self):
        for output in ("", "x 0 YQ==\nx 0 YQ==", "x 0 invalid", "foreign 0 YQ=="):
            with self.subTest(output=output), patch.object(inspection, "read", return_value=output):
                with self.assertRaises((RuntimeError, ValueError)):
                    inspection.shell_probe([], {"x": "true"})

    def test_doctor_stopped_and_wrong_identity_do_not_probe_guest(self):
        with patch.object(inspection, "status", return_value={"state": "shut off"}), \
                patch.object(inspection, "shell_probe") as probe:
            result = inspection.doctor({})
            self.assertEqual(inspection.exit_code({"vm": result}), 2)
            probe.assert_not_called()
        with patch.object(inspection, "status", side_effect=RuntimeError("wrong owner")):
            result = inspection.doctor({})
            self.assertEqual(inspection.exit_code({"vm": result}), 1)

    def test_doctor_interprets_guest_failure_space_and_restart(self):
        def fixture(command, probes):
            result = {key: {"code": 0, "output": ""} for key in probes}
            for key, value in {"hostname": "pi", "architecture": "aarch64", "current": "/a",
                               "default": "/b", "failed": "broken.service loaded failed failed",
                               "services": "active\ninactive", "routes": "[]",
                               "disks": "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/a 100 99 1 99% /",
                               "inodes": "Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/a 100 2 98 2% /"}.items():
                result[key]["output"] = value
            result["reboot"]["code"] = 1
            return result
        with patch.object(inspection, "status", return_value={"state": "running", "addresses": ["10.0.0.2"]}), \
                patch.object(inspection, "shell_probe", side_effect=fixture):
            result = inspection.doctor({"backend": "rpi", "spec": {"hostName": "pi", "diskSerial": "ssd"}})
        checks = {c["name"]: c for c in result["checks"]}
        for name in ("failed", "service:systemd-networkd", "disks:/"):
            self.assertEqual(checks[name]["level"], "error")
        for name in ("generation", "reboot", "route"):
            self.assertEqual(checks[name]["level"], "warning")

    def test_rollback_after_rollback_and_booted_default_are_protected(self):
        systems = {i: "/nix/store/" + str(i) * 32 + "-nixos-system-vm" for i in range(1, 6)}
        def fixture(command, probes):
            return {key: {"code": 0, "output": value} for key, value in {
                "current": systems[2], "booted": systems[1], "default": systems[4],
                "generations": "\n".join(f"/nix/var/nix/profiles/system-{i}-link\t{systems[i]}\t100" for i in range(1, 6))}.items()}
        with patch.object(inspection, "status", return_value={"state": "running", "addresses": ["10.0.0.2"]}), \
                patch.object(inspection, "shell_probe", side_effect=fixture), \
                patch.object(inspection, "read", return_value=json.dumps({s: {"closureSize": 1024} for s in systems.values()})):
            result = inspection.generations({"backend": "libvirt", "spec": {
                "uuid": "315b9a43-1653-4b2f-9cd9-790081000001"}})
        rows = {row["generation"]: row for row in result["generations"]}
        self.assertEqual(rows[1]["protectedBy"], ["booted"])
        self.assertEqual(rows[2]["protectedBy"], ["current"])
        self.assertEqual(rows[4]["protectedBy"], ["default"])
        self.assertEqual(rows[5]["protectedBy"], ["rollback"])
        self.assertEqual(rows[3]["classification"], "review")
        self.assertEqual(rows[3]["closureBytes"], 1024)

    def test_unreachable_target_does_not_hide_other_targets(self):
        registry = {"bad": {"host": "a", "backend": "rpi"}, "good": {"host": "b", "backend": "rpi"}}
        with patch.object(cleanup, "generations", side_effect=[RuntimeError("offline"), {"generations": []}]):
            report = cleanup.preview(registry, list(registry))
        self.assertIn("error", report["targets"]["bad"])
        self.assertEqual(report["targets"]["good"], {"generations": []})
        self.assertTrue(cleanup.failed(report))

    def test_libvirt_real_volume_table_and_backing_references(self):
        domain = '<domain><name>old</name><uuid>uuid</uuid><devices><disk device="disk"><source file="/pool/live.qcow2"/><target dev="vda"/></disk></devices></domain>'
        def respond(command):
            if command[0] == "qemu-img":
                return json.dumps([{"filename": command[-1]}] + ([{"filename": "/pool/base.qcow2"}]
                    if command[-1].endswith("live.qcow2") else []))
            action = command[3]
            if action == "list": return "old"
            if action == "dumpxml": return domain
            if action == "domstate": return "shut off"
            if action == "snapshot-list": return ""
            if action == "domblkinfo": return "Capacity: 200\nAllocation: 100\nPhysical: 100"
            if action == "vol-list":
                self.assertNotIn("--name", command)
                return " Name           Path\n--------------------------------\n live.qcow2   /pool/live.qcow2\n base.qcow2   /pool/base.qcow2\n orphan.qcow2 /pool/orphan.qcow2\n"
            if action == "vol-path": return "/pool/" + command[-1]
            if action == "vol-dumpxml":
                backing = '<backingStore><path>/pool/base.qcow2</path></backingStore>' if command[-1] == "live.qcow2" else ""
                return '<volume><capacity unit="bytes">200</capacity><allocation unit="bytes">100</allocation>' + backing + '</volume>'
            raise AssertionError(command)
        with patch.object(cleanup, "read", side_effect=respond), patch.object(cleanup, "file_size", return_value={"allocatedBytes": 100, "logicalBytes": 200}):
            result = cleanup.libvirt_inventory({"uri": "qemu:///system", "storagePool": "default"}, [])
        self.assertEqual(result["errors"], [])
        self.assertEqual([item["classification"] for item in result["images"]], ["protected", "protected", "review"])
        self.assertEqual(result["machines"][0]["allocatedBytes"], 100)


if __name__ == "__main__":
    unittest.main()
