"""Kontrakty plánování, evidence a ohraničení testovací kampaně."""

import json
import fcntl
from pathlib import Path
import subprocess
import runpy
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch, call

from campaign import Campaign, matrix, nix_attrs, validate_static_network


class CampaignTests(unittest.TestCase):
    def test_cli_pins_its_private_recipe_over_shell_overrides(self):
        task = Campaign('/recipe', '/run-test')
        task.work = Path('/private recipe')
        with patch.dict('os.environ', {'NIXBERRY_RECIPE': '/other', 'NIXON_CONFIG_DIR': '/different'}), \
                patch.object(task, 'command') as command:
            task.cli('show', 'demo')
        self.assertEqual(command.call_args.args[0], ['/private recipe/nixberry', 'show', 'demo'])
        self.assertEqual(command.call_args.kwargs['env']['NIXBERRY_RECIPE'], '/private recipe')

    def test_native_offline_vm_expects_probe_refusal_but_rpi_uses_probe(self):
        for target, code in ((dict(offline=True, backend="proxmox"), 2), (dict(backend="rpi"), 0)):
            task = Campaign("/recipe", "/run-test")
            task.case = dict(target="native")
            with patch.object(task, "evaluate", return_value=target), \
                    patch.object(task, "save"), patch.object(task, "cli", return_value=(0, "{}")) as cli:
                task.native()
            self.assertIn(call("probe", "native", expected=code), cli.call_args_list)
            self.assertTrue(all("--apply" not in call.args for call in cli.call_args_list))

    def test_native_findings_do_not_skip_remaining_readonly_checks(self):
        task = Campaign("/recipe", "/run-test")
        task.case = dict(target="native")
        def cli(command, *args, **kwargs):
            return (2 if command == "doctor" else 0), "{}"
        with patch.object(task, "evaluate", return_value=dict(backend="rpi")), \
                patch.object(task, "save"), patch.object(task, "cli", side_effect=cli) as run, \
                self.assertRaisesRegex(RuntimeError, "doctor=2"):
            task.native()
        self.assertEqual(run.call_args.args[0], "verify")
        self.assertEqual(task.case["nativeFindings"], [dict(command="doctor", returncode=2)])

    def test_unexpected_parser_failure_is_recorded_and_stops_campaign(self):
        with tempfile.TemporaryDirectory() as root:
            task = Campaign(Path(root) / "recipe", Path(root) / "run")
            with patch.object(task, "online", side_effect=ET.ParseError("invalid xml")), \
                    self.assertRaises(ET.ParseError):
                task.run([])
            report = json.loads((task.root / "report.json").read_text())
            self.assertEqual(report["cases"][0]["result"], "FAIL")
            self.assertIn("invalid xml", report["cases"][0]["detail"])

    def test_cleanup_of_already_stopped_vm_does_not_require_guest_ssh(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as root:
                task = Campaign("/recipe", "/run-test")
                task.work = Path(root)
                (task.work / 'instances').mkdir()
                declaration = task.work / 'instances/owned.nix'
                declaration.write_text('{\n  allowReset = false;\n}')
                task.case = dict(target="owned", targetSpec=dict(backend=backend, spec={}))
                with patch.object(task, "snapshot", side_effect=[{"state": "shut off"}, {"state": "absent"}]), \
                        patch.object(task, "poweroff") as poweroff, patch.object(task, "cli") as cli, \
                        patch.object(task, "save"):
                    task.cleanup()
                poweroff.assert_not_called()
                self.assertTrue(task.case["cleaned"])
                self.assertIn('allowReset = true;', declaration.read_text())
                self.assertEqual(cli.call_args_list, [call('reset', 'owned'), call('reset', 'owned', '--apply')])

    def test_proxmox_cleanup_uses_json_api_and_refuses_snapshots(self):
        for snapshots in ([{"name": "current"}], [{"name": "current"}, {"name": "keep"}]):
            with self.subTest(snapshots=snapshots), tempfile.TemporaryDirectory() as root:
                directory = Path(root) / "owned"
                directory.mkdir()
                calls = []
                def run(argv):
                    calls.append(argv)
                    return json.dumps(snapshots) if argv[0] == "pvesh" else ""
                module = runpy.run_path(str(Path(__file__).with_name("campaign_host.py")), init_globals={
                    "_": lambda value: value, "json": json, "shutil": __import__("shutil"),
                    "host_check": lambda *args: directory, "probe": lambda *args: {"state": "shut off"},
                    "owned": lambda *args: None, "run": run,
                })
                request = dict(backend="proxmox", action="cleanup", spec=dict(
                    name="nixberry-vm-demo-test-owned", uuid="own-uuid", node="pvelab", vmid=1000))
                if len(snapshots) == 1:
                    module["campaign_action"](request)
                    self.assertEqual(calls[-1], ["qm", "destroy", "1000", "--purge", "1"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "snapshot"):
                        module["campaign_action"](request)
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(directory.exists())
                self.assertEqual(calls[0], ["pvesh", "get", "/nodes/pvelab/qemu/1000/snapshot", "--output-format", "json"])

    def test_handoff_waits_for_ssh_after_dhcp_without_changing_command(self):
        task = Campaign("/recipe", "/run-test")
        command = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "root@192.0.2.1", "hostname"]
        with patch.object(task, "command", side_effect=[(255, ""), (0, "guest")]) as run, \
                patch("campaign.time.sleep"):
            task.wait_guest_ssh(command)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.args == (command,) for call in run.call_args_list))

    def test_static_network_requires_address_prefix_gateway_interface_and_dns(self):
        expected = dict(address="192.168.203.64/24", gateway="192.168.203.2", dns=["192.168.203.2"])
        addresses = [{"ifname": "ens33", "addr_info": [{"local": "192.168.203.64", "prefixlen": 24}]}]
        routes = [{"gateway": "192.168.203.2", "dev": "ens33"}]
        validate_static_network(expected, addresses, routes, "Link 2 (ens33): 192.168.203.2")
        for change in (dict(address="192.168.203.128/24"), dict(address="192.168.203.64/16"),
                       dict(gateway="192.168.203.1"), dict(dns=["1.1.1.1"])):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_static_network({**expected, **change}, addresses, routes, "nameserver 192.168.203.2")
        with self.assertRaises(RuntimeError):
            validate_static_network(expected, addresses, [{**routes[0], "dev": "ens34"}], "192.168.203.2")

    def test_retry_does_not_inherit_cleaned_flag_from_previous_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            task = Campaign(Path(root) / "recipe", Path(root) / "run")
            def first():
                task.case.update(writeStarted=True, cleaned=True)
                raise RuntimeError("first failure already cleaned")
            with patch.object(task, "online", side_effect=first), self.assertRaises(RuntimeError):
                task.run([])
            def second():
                self.assertNotIn("cleaned", task.case)
                task.case["writeStarted"] = True
                raise RuntimeError("second failure preserves new VM")
            with patch.object(task, "online", side_effect=second), self.assertRaises(RuntimeError):
                task.run([], resume=True)
            with patch.object(task, "online") as online, self.assertRaisesRegex(RuntimeError, "retained resources"):
                task.run([], resume=True)
            online.assert_not_called()

    def test_retry_preserves_orphan_directory_after_early_interruption(self):
        with tempfile.TemporaryDirectory() as root:
            task = Campaign(Path(root) / "recipe", Path(root) / "run")
            orphan = task.root / matrix()[0]["id"] / "attempt-1"
            orphan.mkdir(parents=True)
            (orphan / "evidence").write_text("keep")
            (task.root / "report.json").write_text(json.dumps({"cases": [{**matrix()[0], "result": "NOT_RUN"}]}))
            with patch.object(task, "online"):
                task.run([], resume=True)
            self.assertEqual((orphan / "evidence").read_text(), "keep")
            self.assertEqual(task.case["attempt"], 2)

    def test_shutdown_connection_loss_requires_hypervisor_confirmation(self):
        task = Campaign("/recipe", "/run-test")
        with patch.object(task, "guest", return_value=(255, "")), patch.object(task, "wait_off") as wait:
            task.poweroff()
            wait.assert_called_once_with()
        with patch.object(task, "guest", return_value=(255, "")), \
                patch.object(task, "wait_off", side_effect=RuntimeError("still running")):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                task.poweroff()

    def test_concurrent_run_is_rejected_before_report_changes(self):
        with tempfile.TemporaryDirectory() as root:
            task = Campaign(Path(root) / "recipe", Path(root) / "run")
            with (Path(root) / "run.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(task, "_run") as run, self.assertRaisesRegex(RuntimeError, "Another process"):
                    task.run([])
                run.assert_not_called()
            self.assertFalse(task.root.exists())

    def test_matrix_has_exact_approved_coverage(self):
        cases = matrix()
        self.assertEqual(len(cases), 32)
        self.assertEqual(len({c["id"] for c in cases}), 32)
        self.assertEqual({g: sum(c["group"] == g for c in cases) for g in ("ON", "OFF", "NET", "HAND", "NATIVE")},
                         {"ON": 14, "OFF": 10, "NET": 2, "HAND": 4, "NATIVE": 2})
        for host in {c["host"] for c in cases if c["group"] == "ON"}:
            self.assertEqual({c["method"] for c in cases if c.get("host") == host and c["group"] == "ON"}, {"image", "iso"})

    def test_default_invocation_only_prints_plan(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(__file__).with_name("campaign.py")
            result = subprocess.run([sys.executable, str(path), "--run-root", root + "/untouched"],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(len(json.loads(result.stdout)), 32)
            self.assertFalse(Path(root, "untouched").exists())

    def test_report_cannot_live_inside_recipe(self):
        with tempfile.TemporaryDirectory() as recipe:
            with self.assertRaises(ValueError):
                Campaign(recipe, Path(recipe) / "results")

    def test_case_failure_is_saved_and_stops_further_writes(self):
        with tempfile.TemporaryDirectory() as root:
            task = Campaign(Path(root) / "recipe", Path(root) / "run")
            with patch.object(task, "online", side_effect=RuntimeError("expected failure")) as online:
                with self.assertRaisesRegex(RuntimeError, "expected failure"):
                    task.run([])
            self.assertEqual(online.call_count, 1)
            report = json.loads((task.root / "report.json").read_text())
            self.assertEqual(report["cases"][0]["result"], "FAIL")
            self.assertTrue(all(c["result"] == "NOT_RUN" for c in report["cases"][1:]))

    def test_resume_refuses_uncertain_existing_resources(self):
        with tempfile.TemporaryDirectory() as root:
            task = Campaign(Path(root) / "recipe", Path(root) / "run")
            task.root.mkdir()
            case = {**matrix()[0], "result": "FAIL", "writeStarted": True}
            (task.root / "report.json").write_text(json.dumps({"cases": [case]}))
            with patch.object(task, "online") as online, self.assertRaises(RuntimeError):
                task.run([], resume=True)
            online.assert_not_called()

    def test_guest_program_preserves_shell_quoting(self):
        task = Campaign("/recipe", "/run-test")
        task.case = {"target": "example"}
        with patch.object(task, "cli") as cli:
            task.guest("test \"$(hostname)\" = example; printf '%s\\n' ready")
        self.assertEqual(cli.call_args.args[0:3], ("ssh", "example", "--"))
        self.assertEqual(len(cli.call_args.args), 4)
        self.assertIn('"$(hostname)"', cli.call_args.args[3])

    def test_network_values_cannot_inject_nix_interpolation(self):
        self.assertIn('"\\${danger}"', nix_attrs({"address": "${danger}", "dns": ["1.2.3.4"]}))

    def test_cleanup_rejects_links_before_native_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "vm"
            directory.mkdir()
            (directory / "outside").symlink_to(Path(root))
            native_calls = []
            module = runpy.run_path(str(Path(__file__).with_name("campaign_host.py")), init_globals={
                "_": lambda value: value, "host_check": lambda *args: directory,
                "probe": lambda *args: {"state": "shut off"}, "owned": lambda *args: None,
                "run": lambda args: native_calls.append(args),
            })
            with self.assertRaisesRegex(RuntimeError, "link"):
                module["campaign_action"]({"backend": "virtualbox", "action": "cleanup",
                    "spec": {"name": "nixberry-vm-demo-test-owned"}})
            self.assertEqual(native_calls, [])
            self.assertTrue(directory.exists())


if __name__ == "__main__":
    unittest.main()
