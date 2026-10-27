#!/usr/bin/env python3
"""Ověření, že společný vstup nepovolí nechtěný zápis nebo jiný cíl."""

import contextlib
import ast
import gettext
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

MAIN = runpy.run_path(str(Path(__file__).with_name("cli.py")))["main"]


class NamedShowChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).with_name("locales") / "cs/LC_MESSAGES/nixberry.po"
        result = subprocess.run(["msgfmt", "--check", "-o", "-", str(source)], capture_output=True, check=True)
        cls.czech = gettext.GNUTranslations(io.BytesIO(result.stdout))

    def rpi(self):
        return {"backend": "rpi", "host": "rpi-demo", "profile": "rpi-demo", "configuration": "default",
                "capabilities": {"build": True, "export": True, "hypervisor": False, "system": True}, "commands": {"probe": "/unused"},
                "security": {"mode": "luks", "interface": "wan0"},
                "spec": {"hostName": "rpi-demo", "address": "192.168.50.1/24", "hardwareSerial": "0000000000000001",
                         "diskDevice": "/dev/disk/by-id/usb-EXAMPLE", "network": {
                             "primary": "lan0", "recoveryInterface": "lan0", "dns": ["1.1.1.1"], "interfaces": {
                                 "lan0": {"mac": "02:00:00:00:00:01", "mode": "static", "address": "192.168.50.1/24", "gateway": ""},
                                 "wan0": {"mac": "02:00:00:00:00:02", "mode": "static", "address": "10.0.0.2/24", "gateway": "10.0.0.1"}}}}}

    def vm(self, named=True):
        target = {"backend": "libvirt", "host": "test-host", "profile": "nixberry-vm", "configuration": "default",
                  "capabilities": {"build": True, "export": True, "hypervisor": True, "system": True}, "commands": {"probe": "/unused"},
                  "network": {"mode": "dhcp", "interfaces": {"lan0": {"mode": "static", "address": "192.168.50.1/24"}}},
                  "spec": {"name": "vm-demo-test", "vcpus": 2, "memoryMiB": 2048, "diskGiB": 16}}
        if named:
            target["spec"]["networkAdapters"] = [{"name": "wan0", "mac": "52:54:00:00:00:01"},
                                                   {"name": "lan0", "mac": "52:54:00:00:00:02"}]
        return target

    def show(self, target, language="en", as_json=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.json"
            registry.write_text(json.dumps({"test": target}))
            return self.show_from_registry(target, language, as_json, registry, stdout, stderr)

    def show_from_registry(self, target, language, as_json, registry, stdout, stderr):
        with patch.dict(MAIN.__globals__, {"operations": Mock(return_value=["show"])}), \
                patch.dict(os.environ, {"NIXBERRY_LANG": language, "NIXBERRY_DEPLOYMENT": str(registry)}), \
                patch("messages._catalog", side_effect=lambda lang: self.czech if lang == "cs" else gettext.NullTranslations()), \
                patch("subprocess.run", side_effect=AssertionError("show must not contact machines")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(MAIN(["show", "test", *(["--json"] if as_json else [])]), 0)
        self.assertNotIn("[nixberry]", stdout.getvalue())
        return stdout.getvalue()

    def test_named_rpi_show_explains_management_recovery_and_unlock_in_english_and_czech(self):
        for language, primary, recovery, unlock, row in [
            ("en", "Primary interface: lan0", "Recovery interface: lan0", "Early-unlock interface: wan0", "Interface wan0: MAC"),
            ("cs", "Primární rozhraní: lan0", "Obnovovací rozhraní: lan0", "Rozhraní počátečního odemčení: wan0", "Rozhraní wan0: MAC"),
        ]:
            with self.subTest(language=language):
                output = self.show(self.rpi(), language)
                for expected in [primary, recovery, unlock, row, "10.0.0.2/24", "10.0.0.1", "luks"]:
                    self.assertIn(expected, output)
        self.assertIn("Security: luks; unlock: manual", self.show(self.rpi()))
        target = self.rpi()
        target["security"].update(interface="lan0", address="192.168.50.59/24")
        self.assertIn("Early-unlock address: 192.168.50.59/24", self.show(target))
        self.assertIn("Adresa počátečního odemčení: 192.168.50.59/24", self.show(target, "cs"))
        self.assertIn("address 192.168.50.1/24", self.show(target))

    def test_named_vm_show_and_legacy_output_boundary(self):
        for language, primary, row in [("en", "Primary interface: wan0", "Interface lan0: MAC"),
                                       ("cs", "Primární rozhraní: wan0", "Rozhraní lan0: MAC")]:
            with self.subTest(language=language):
                output = self.show(self.vm(), language)
                self.assertIn(primary, output)
                self.assertIn(row, output)
                self.assertIn("192.168.50.1/24", output)
                self.assertIn("dhcp", output)
                self.assertNotIn("Recovery interface:", output)
        output = self.show(self.vm(named=False))
        self.assertIn("Network: DHCP", output)
        self.assertNotIn("Primary interface:", output)
        self.assertNotIn("MAC", output)

    def test_show_json_keeps_the_original_data_for_both_languages_and_platforms(self):
        for target in [self.rpi(), self.vm()]:
            for language in ["en", "cs"]:
                with self.subTest(backend=target["backend"], language=language):
                    output = self.show(target, language, as_json=True)
                    self.assertEqual(json.loads(output), {**{key: value for key, value in target.items() if key != "commands"},
                                                         "operations": ["show"]})


class HelpChecks(unittest.TestCase):
    def test_help_describes_all_commands_without_registry_or_network(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
                patch("pathlib.Path.read_text", side_effect=AssertionError("Nápověda nesmí číst evidenci")), \
                patch("subprocess.run") as run, contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                MAIN(["--help"])
        self.assertEqual(result.exception.code, 0)
        run.assert_not_called()
        text = output.getvalue()
        tree = ast.parse(Path(__file__).with_name("cli.py").read_text())
        actions = next(ast.literal_eval(keyword.value)
                       for node in ast.walk(tree) if isinstance(node, ast.Call)
                       for keyword in node.keywords if keyword.arg == "choices")
        for action in actions:
            self.assertRegex(text, r"(?m)^  " + action + r"(?:\s|$)")
        self.assertIn("nixberry update vm-demo-libvirt --apply", text)
        self.assertIn("BEFORE an update", text)
        self.assertIn("The two highest retained server generations", text)
        self.assertIn("docs/cli.md", text)
        self.assertIn("new-* and export normally write locally", text)


class DispatchChecks(unittest.TestCase):
    def test_encrypted_rpi_paths_are_forwarded_without_reading_credentials(self):
        registry = {"rpi-demo": {"backend": "rpi", "spec": {"hostName": "rpi-demo"},
                                 "security": {"mode": "luks"}}}
        with patch("pathlib.Path.read_text", return_value=json.dumps(registry)), \
                patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}), \
                patch.dict(MAIN.__globals__, {"materialized_recipe": lambda _: contextlib.nullcontext("/recipe")}), \
                patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            for mode in ([], ["--apply"]):
                self.assertEqual(MAIN(["reproduce", "rpi-demo", "--key-file", "/missing/password",
                    "--initrd-key-file", "/missing/initrd-key", *mode]), 0)
                command = run.call_args.args[0]
                self.assertIn("--apply" if mode else "--dry-run", command)
                self.assertEqual(command[command.index("--key-file") + 1], "/missing/password")
                self.assertEqual(command[command.index("--initrd-key-file") + 1], "/missing/initrd-key")

    def test_encrypted_declaration_hints_prepare_before_plan_or_export(self):
        create = Mock(return_value=(Path("/recipe/instances/vm-demo-secured.nix"), "{}"))
        with patch("pathlib.Path.read_text", return_value="{}"), \
                patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT_HOSTS": "/hosts"}), \
                patch.dict(MAIN.__globals__, {"new_vm": create, "new_libvirt": create}), \
                patch("subprocess.run") as run:
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                for offline in ([], ["--offline"]):
                    with self.subTest(backend=backend, offline=offline), \
                            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as hints:
                        self.assertEqual(MAIN(["new-" + backend + "-vm", "vm-demo-secured",
                                               "--security", "luks", "--dry-run", *offline]), 0)
                        text = hints.getvalue()
                        following = "export" if offline else "plan"
                        self.assertIn("Next step: nixberry prepare-image vm-demo-secured", text)
                        self.assertLess(text.index("nixberry prepare-image"), text.index("nixberry " + following))
            run.assert_not_called()

    def test_missing_encrypted_image_stops_before_any_backend_dispatch(self):
        manifest = Path(os.environ["NIXBERRY_DEPLOYMENT"])
        with patch("subprocess.run") as run:
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                manifest.write_text(json.dumps({"test": {"backend": backend, "spec": {}, "host": "lab",
                    "commands": {action: "/tools/" + action for action in ("create", "install", "provision")},
                    "security": {"mode": "luks", "imageDirectory": str(manifest.parent / "not-prepared")}}}))
                for action in ("plan", "create", "install", "provision", "reproduce", "export", "unlock"):
                    extra = ["--address", "192.0.2.20"] if action == "unlock" else []
                    with self.subTest(backend=backend, action=action):
                        with self.assertRaises(RuntimeError) as failure:
                            MAIN([action, "test", *extra])
                        self.assertIn("nixberry prepare-image test", str(failure.exception))
                        self.assertNotIn("Errno", str(failure.exception))
            run.assert_not_called()

    def test_configuration_is_persisted_by_all_new_commands_and_not_an_update_override(self):
        create = Mock(return_value=(Path("/recipe/instances/demo.nix"), "{}"))
        with patch("pathlib.Path.read_text", return_value="{}"), \
                patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT_HOSTS": "/hosts"}), \
                patch.dict(MAIN.__globals__, {"new_vm": create, "new_libvirt": create}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                for offline in ([], ["--offline"]):
                    self.assertEqual(MAIN(["new-" + backend + "-vm", "demo", "--configuration", "web", "--dry-run", *offline]), 0)
                    self.assertEqual(create.call_args.kwargs["configuration"], "web")
            with self.assertRaises(SystemExit) as result:
                MAIN(["update", "demo", "--configuration", "web", "--apply"])
            self.assertEqual(result.exception.code, 2)

    def test_every_new_vm_supports_offline_without_live_dispatch(self):
        create = Mock(return_value=(Path("/recipe/instances/offline.nix"), 'backend = "libvirt";'))
        with patch("pathlib.Path.read_text", return_value="{}"), \
                patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT_HOSTS": "/hosts"}), \
                patch.dict(MAIN.__globals__, {"new_vm": create}), patch("subprocess.run") as run:
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                for suffix in ("", "-vm"):
                    with self.subTest(backend=backend, suffix=suffix), \
                            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as hints:
                        self.assertEqual(MAIN(["new-" + backend + suffix, "offline", "--offline", "--dry-run"]), 0)
                        self.assertTrue(create.call_args.kwargs["offline"])
                        self.assertEqual(create.call_args.args[4], backend)
                        self.assertIn("nixberry export offline --output ~/offline-offline", hints.getvalue())
                        self.assertNotIn("nixberry reproduce", hints.getvalue())
            run.assert_not_called()

    def test_new_vm_names_and_legacy_aliases_select_online_hints(self):
        create = Mock(return_value=(Path("/recipe/instances/example.nix"), 'host = "pve";'))
        with patch("pathlib.Path.read_text", return_value="{}"), \
                patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT_HOSTS": "/hosts"}), \
                patch.dict(MAIN.__globals__, {"new_vm": create, "new_libvirt": create}):
            for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
                for suffix in ("", "-vm"):
                    with self.subTest(backend=backend, suffix=suffix), \
                            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as hints:
                        self.assertEqual(MAIN(["new-" + backend + suffix, "example", "--dry-run"]), 0)
                        self.assertIn("nixberry plan example", hints.getvalue())
                        self.assertIn("nixberry reproduce example --apply", hints.getvalue())
                        self.assertNotIn("nixberry export", hints.getvalue())

    def test_offline_virtualbox_scaffold_dispatch_and_invalid_combinations(self):
        create = Mock(return_value=(Path("/recipe/instances/offline.nix"), 'backend = "virtualbox";'))
        with patch("pathlib.Path.read_text", return_value="{}"), \
                patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT": "/registry", "NIXBERRY_DEPLOYMENT_HOSTS": "/hosts"}), \
                patch.dict(MAIN.__globals__, {"new_vm": create}), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as hints:
            self.assertEqual(MAIN(["new-virtualbox", "offline", "--offline", "--dry-run"]), 0)
            self.assertTrue(create.call_args.kwargs["offline"])
            self.assertFalse(create.call_args.kwargs["write"])
            self.assertIn("backend", output.getvalue())
            self.assertIn("nixberry export offline", hints.getvalue())
            for args in (["show", "test", "--offline"],
                         ["new-virtualbox", "test", "--offline", "--host", "hostitel"]):
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    MAIN(args)
                self.assertEqual(error.exception.code, 2)

    def test_verify_failure_keeps_direct_endpoint_in_recovery_commands(self):
        fixture = {"test": {"backend": "proxmox", "offline": True, "host": "offline:test", "spec": {}}}
        verify = Mock(side_effect=RuntimeError("The running system does not match the recipe."))
        with patch("pathlib.Path.read_text", return_value=json.dumps(fixture)), \
                patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}), \
                patch.dict(MAIN.__globals__, {"verify": verify, "read": Mock(return_value="/nix/store/expected")}):
            with self.assertRaises(RuntimeError) as error:
                MAIN(["verify", "test", "--ssh", "root@203.0.113.19:2030"])
        self.assertIn("nixberry doctor test --ssh root@203.0.113.19:2030", str(error.exception))
        self.assertIn("nixberry diff test --ssh root@203.0.113.19:2030", str(error.exception))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        manifest = Path(self.directory.name) / "registry.json"
        manifest.write_text(json.dumps({"test": {
            "host": "host", "backend": "hyperv", "profile": "profile",
            "spec": {"name": "test-vm", "vcpus": 4, "memoryMiB": 4096, "diskGiB": 32},
            "commands": {"provision": "/tools/provision", "probe": "/tools/probe"},
        }}))
        self.env = patch.dict(os.environ, {"NIXBERRY_DEPLOYMENT": str(manifest)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_entrypoint_error_is_plain_stderr_with_nonzero_exit(self):
        with patch.dict(os.environ, {"NIXBERRY_RECIPE": "/missing-nixberry-recipe"}):
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("cli.py")),
                                     "update", "test"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("[nixberry] Run update through the nixberry launcher", result.stderr)
        self.assertNotIn("\033[", result.stderr)

    def test_default_and_plan_cannot_write(self):
        for args in (["provision", "test"], ["plan", "test"], ["reproduce", "test"]):
            with self.subTest(args=args), patch("subprocess.run") as run:
                MAIN(args)
                self.assertEqual(run.call_args.args[0], ["/tools/provision", "--dry-run"])

    def test_apply_is_explicit(self):
        with patch("subprocess.run") as run:
            MAIN(["provision", "test", "--apply"])
            self.assertEqual(run.call_args.args[0], ["/tools/provision", "--apply"])

    def test_rejected_requests_never_dispatch(self):
        for args in (["provision", "192.168.79.1", "--apply"],
                     ["reset", "test", "--apply"], ["plan", "test", "--apply"],
                     ["probe", "test", "--apply"], ["show", "test", "--apply"],
                     ["status", "--apply"], ["ssh", "test", "--apply"],
                     ["ssh", "test", "--dry-run"], ["provision", "test", "--json"],
                     ["provision", "test", "--", "--apply"],
                     ["new-libvirt", "lab", "--apply"],
                     ["reproduce", "test", "--erase-existing"],
                     ["list", "--apply"], ["provision", "test", "--apply", "--dry-run"]):
            with self.subTest(args=args), patch("subprocess.run") as run:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    MAIN(args)
                run.assert_not_called()

    def test_inventory_does_not_connect(self):
        with patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            MAIN(["list"])
            MAIN(["show", "test"])
            run.assert_not_called()

    def test_direct_host_override_keeps_update_dry_by_default(self):
        fixture = {"test": {"backend": "proxmox", "offline": True, "host": "offline:test", "spec": {}}}
        update = Mock()
        with patch("pathlib.Path.read_text", return_value=json.dumps(fixture)), \
                patch.dict(MAIN.__globals__, {"update": update}), patch("subprocess.run") as run:
            for extra, apply in (([], False), (["--apply"], True)):
                self.assertEqual(MAIN(["update", "test", "--host", "root@203.0.113.19:2030", *extra]), 0)
                self.assertEqual(update.call_args.kwargs, {"apply": apply})
                target = update.call_args.args[2]
                self.assertTrue(target["offline"])
                self.assertEqual(target["_guestConnection"]["port"], 2030)
            run.assert_not_called()
            for action in ("create", "install", "reproduce", "reset", "cleanup", "export"):
                with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    MAIN([action, "test", "--host", "root@host"])
        with patch("pathlib.Path.read_text", return_value=json.dumps({"pi": {"backend": "rpi"}})), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MAIN(["update", "pi", "--host", "root@host"])
        with patch("pathlib.Path.read_text", return_value=json.dumps({"test": {
                "backend": "proxmox", "offline": False}})), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MAIN(["update", "test", "--host", "root@host"])

    def test_diff_dispatch_and_rejected_write_modes(self):
        compare = Mock(return_value=7)
        with patch.dict(MAIN.__globals__, {"compare": compare}), \
                patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}):
            for action in ("diff", "diffg"):
                self.assertEqual(MAIN([action, "test"]), 7)
                self.assertEqual(compare.call_args.args[:2], ("/recipe", "test"))
                self.assertEqual(compare.call_args.kwargs,
                                 {"generations": action == "diffg", "dry_run": False})
                MAIN([action, "test", "--dry-run"])
                self.assertTrue(compare.call_args.kwargs["dry_run"])
                compare.reset_mock()
                for extra in ("--apply", "--json", "--address"):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        MAIN([action, "test", extra])
                compare.assert_not_called()

    def test_empty_inventory_is_usable_without_connections(self):
        with patch("pathlib.Path.read_text", return_value="{}"), patch("subprocess.run") as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(MAIN(["list"]), 0)
                self.assertEqual(MAIN(["status"]), 0)
            run.assert_not_called()

    def test_verify_selects_the_declared_target_system(self):
        fixture = {"hostname": "custom", "root": "/dev/sda2", "system": "/nix/store/custom"}
        with patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}), \
                patch.dict(MAIN.__globals__, {"read": lambda cmd: (
                    "/nix/store/custom" if "#nixberrySystems.test." in cmd[-1]
                    else (_ for _ in ()).throw(AssertionError(cmd))),
                    "verify": lambda target, expected: fixture if expected == fixture["system"]
                    else (_ for _ in ()).throw(AssertionError(expected))}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MAIN(["verify", "test"]), 0)

    def test_update_requires_explicit_apply_and_selected_target(self):
        update = Mock()
        with patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}), \
                patch.dict(MAIN.__globals__, {"update": update}):
            for args, apply in ((["update", "test"], False),
                                (["update", "test", "--dry-run"], False),
                                (["update", "test", "--apply"], True)):
                self.assertEqual(MAIN(args), 0)
                self.assertEqual(update.call_args.args[:2], ("/recipe", "test"))
                self.assertEqual(update.call_args.kwargs, {"apply": apply})
            update.reset_mock()
            for args in (["update"], ["update", "192.168.79.1", "--apply"],
                         ["update", "test", "--json"], ["update", "test", "--address", "10.0.0.2"],
                         ["update", "test", "--erase-existing"], ["update", "test", "--", "reboot"]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    MAIN(args)
            update.assert_not_called()

    def test_one_unreachable_host_does_not_hide_other_hosts(self):
        fixture = {"first": {"host": "one", "backend": "rpi"},
                   "second": {"host": "two", "backend": "rpi"}}
        with patch.dict(MAIN.__globals__, {"status": lambda target: (
                {"state": "running", "addresses": ["192.168.77.97"]} if target["host"] == "two"
                else (_ for _ in ()).throw(RuntimeError("SSH nedostupné")))}):
            with patch("pathlib.Path.read_text", return_value=json.dumps(fixture)):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(MAIN(["status", "--json"]), 1)
                data = json.loads(output.getvalue())
                self.assertEqual(data["first"]["state"], "error")
                self.assertEqual(data["second"]["state"], "running")

    def test_probe_and_exit_status(self):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 7
            self.assertEqual(MAIN(["probe", "test"]), 7)
            self.assertEqual(run.call_args.args[0], ["/tools/probe"])

    def test_doctor_cleanup_cannot_apply_or_accept_arbitrary_target(self):
        with patch("subprocess.run") as run:
            for action in ("doctor", "cleanup"):
                for args in ([action, "test", "--apply"], [action, "192.168.79.1"],
                             [action, "test", "--", "reboot"]):
                    with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        MAIN(args)
            run.assert_not_called()

    def test_doctor_dispatch_and_exit_status(self):
        fixture = {"checks": [{"name": "disk", "level": "warning", "message": "90 %"}]}
        with patch.dict(MAIN.__globals__, {"doctor": lambda _: fixture}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(MAIN(["doctor", "test", "--json"]), 2)
        self.assertEqual(json.loads(output.getvalue()), {"test": fixture})

    def test_cleanup_empty_inventory_and_explicit_root(self):
        fixture = {"readOnly": True, "targets": {}, "hosts": {}, "artifacts": {"images": [], "errors": []}}
        preview = Mock(return_value=fixture)
        with patch.dict(MAIN.__globals__, {"preview": preview}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(MAIN(["cleanup", "--artifact-root", "/images", "--json"]), 0)
        self.assertEqual(preview.call_args.args[1:], (["test"], ["/images"]))
        self.assertEqual(json.loads(output.getvalue()), fixture)

    def test_all_new_backends_require_local_declaration_and_reject_apply(self):
        for backend in ("hyperv", "proxmox", "virtualbox", "vmware"):
            with self.subTest(backend=backend), patch("subprocess.run") as run:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    MAIN(["new-" + backend, "example", "--apply"])
                run.assert_not_called()

    def test_new_hyperv_and_proxmox_create_only_local_declarations(self):
        root = Path(self.directory.name)
        (root / "flake.nix").touch()
        (root / "configurations").mkdir()
        (root / "configurations/default.nix").write_text("{}")
        directory = root / "instances"
        directory.mkdir(parents=True)
        (directory / "default.nix").touch()
        hosts = root / "hosts.json"
        hosts.write_text(json.dumps({"windows": {"backend": "hyperv"}, "pve": {"backend": "proxmox"}}))
        with patch.dict(os.environ, {"NIXBERRY_RECIPE": str(root), "NIXBERRY_DEPLOYMENT_HOSTS": str(hosts)}), \
                patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            for backend, host in (("hyperv", "windows"), ("proxmox", "pve")):
                path = directory / (backend + ".nix")
                self.assertEqual(MAIN(["new-" + backend, backend, "--dry-run"]), 0)
                self.assertFalse(path.exists())
                self.assertEqual(MAIN(["new-" + backend, backend, "--host", host]), 0)
                self.assertTrue(path.exists())
            run.assert_not_called()

    def test_offline_proxmox_rejects_live_operations_and_allows_export(self):
        target = {"host": "offline:test", "backend": "proxmox", "offline": True,
                  "profile": "nixberry-proxmox", "commands": {}, "operations": ["show", "export"],
                  "spec": {"name": "nixberry-test", "vmid": 1000, "storage": "local-lvm",
                           "bridge": "vmbr0", "vcpus": 4, "memoryMiB": 4096, "diskGiB": 32}}
        exporter = Mock(return_value={"destination": "/bundle"})
        with patch("pathlib.Path.read_text", return_value=json.dumps({"test": target})), \
                patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}), \
                patch.dict(MAIN.__globals__, {"export": exporter}), patch("subprocess.run") as run:
            with contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()) as messages:
                self.assertEqual(MAIN(["show", "test"]), 0)
                self.assertEqual(MAIN(["export", "test", "--output", "/bundle", "--dry-run"]), 0)
            self.assertIn("offline export", messages.getvalue())
            self.assertTrue(exporter.call_args.kwargs["dry_run"])
            with contextlib.redirect_stdout(io.StringIO()) as inventory:
                self.assertEqual(MAIN(["status", "test", "--json"]), 0)
                self.assertEqual(json.loads(inventory.getvalue())["test"]["state"], "unconfigured")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(MAIN(["doctor", "test"]), 2)
            for action in ("plan", "ssh", "reproduce", "update", "verify", "cleanup"):
                with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    MAIN([action, "test"])
            run.assert_not_called()

    def test_ssh_command_is_separate_from_local_flags(self):
        snapshot = {"state": "running", "addresses": ["10.0.0.2"]}
        with patch.dict(MAIN.__globals__, {"status": lambda _: snapshot,
                                          "ssh_command": lambda *_: ["ssh", "--", "root@10.0.0.2"]}):
            with patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
                MAIN(["ssh", "test", "--", "hostname"])
                self.assertEqual(run.call_args.args[0], ["ssh", "--", "root@10.0.0.2", "hostname"])

    def test_methods_are_forwarded_only_for_deployment_and_shown_in_json(self):
        with patch("subprocess.run", return_value=Mock(returncode=0)) as run, \
                contextlib.redirect_stderr(io.StringIO()):
            for action in ("plan", "reproduce", "provision"):
                self.assertEqual(MAIN([action, "test", "--method", "iso"]), 0)
                self.assertEqual(run.call_args.args[0], ["/tools/provision", "--method", "iso", "--dry-run"])
            for action in ("status", "ssh", "update", "reset"):
                with self.assertRaises(SystemExit):
                    MAIN([action, "test", "--method", "iso"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            MAIN(["show", "test", "--method", "iso", "--json"])
        self.assertEqual(json.loads(output.getvalue())["deploymentMethod"], "iso")

    def test_interactive_ssh_explains_and_enables_local_disconnect(self):
        snapshot = {"state": "running", "addresses": ["10.0.0.2"]}
        with patch.dict(MAIN.__globals__, {"status": lambda _: snapshot,
                                          "ssh_command": lambda *_: ["ssh", "--", "root@10.0.0.2"]}), \
                patch("sys.stdin.isatty", return_value=True), \
                patch("subprocess.run", return_value=Mock(returncode=255)) as run, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(MAIN(["ssh", "test"]), 255)
            self.assertEqual(run.call_args.args[0], ["ssh", "-e", "~", "--", "root@10.0.0.2"])
            self.assertIn("Enter, then ~.", output.getvalue())
            output.truncate(0)
            output.seek(0)
            MAIN(["ssh", "test", "--", "hostname"])
            self.assertNotIn("Enter, then ~.", output.getvalue())
            self.assertNotIn("-e", run.call_args.args[0])

    def test_ssh_interrupted_before_remote_terminal_returns_130(self):
        with patch.dict(MAIN.__globals__, {"status": lambda _: {"state": "running"},
                                          "ssh_command": lambda *_: ["ssh", "--", "root@10.0.0.2"]}), \
                patch("subprocess.run", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MAIN(["ssh", "test"]), 130)

    def test_rpi_details_and_unsupported_install_never_dispatch(self):
        fixture = {"nixberry": {"host": "nixberry", "backend": "rpi", "profile": "nixberry",
            "commands": {}, "spec": {"hostName": "nixberry", "address": "192.168.50.20/24",
                "hardwareSerial": "serial", "diskDevice": "/dev/disk/by-id/test"}}}
        with patch("pathlib.Path.read_text", return_value=json.dumps(fixture)), patch("subprocess.run") as run:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                MAIN(["show", "nixberry"])
            self.assertIn("RPi: nixberry", output.getvalue())
            for action in ("create", "install", "provision", "reset"):
                with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        MAIN([action, "nixberry", "--apply"])
            run.assert_not_called()

    def test_rpi_reproduction_uses_existing_guarded_installer(self):
        fixture = {"nixberry": {"backend": "rpi", "commands": {}, "host": "nixberry"}}
        with patch("pathlib.Path.read_text", return_value=json.dumps(fixture)):
            with patch.dict(os.environ, {"NIXBERRY_RECIPE": "/recipe"}), patch("subprocess.run") as run:
                MAIN(["plan", "nixberry", "--address", "192.168.79.229", "--erase-existing"])
                command = run.call_args.args[0]
                self.assertEqual(command[3:5], ["path:/recipe#nixberry-rpi-install", "--"])
                self.assertIn("--dry-run", command)
                self.assertNotIn("--apply", command)
                self.assertEqual(command[-3:], ["--address", "192.168.79.229", "--erase-existing"])
                MAIN(["reproduce", "nixberry", "--apply"])
                self.assertIn("--apply", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
