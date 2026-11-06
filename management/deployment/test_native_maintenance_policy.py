"""Configuration-owned maintenance stays optional and preserves backup v1."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from recipe_source import recipe_uri
from scaffold import new_vm
from test_support import copy_test_recipe


class NativeMaintenancePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="nixberry-maintenance-policy-")
        cls.addClassCleanup(temporary.cleanup)
        cls.recipe = Path(temporary.name) / "recipe"
        copy_test_recipe(Path(__file__).resolve().parents[2], cls.recipe)
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            new_vm(cls.recipe, {}, {}, "vm-demo-" + backend, backend, offline=True)
        original = (cls.recipe / "examples/rpi.nix").read_text()
        (cls.recipe / "machines/rpi/rpi-demo.nix").write_text(original)

    def evaluate(self, expression):
        result = subprocess.run([
            "nix", "eval", "--offline", "--impure", "--json", "--no-write-lock-file", "--expr",
            "let f = builtins.getFlake " + json.dumps(recipe_uri(self.recipe)) + "; in " + expression,
        ], text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-6000:])
        return json.loads(result.stdout)

    def settings(self, restic="", *, enabled=True, prepared=True):
        return '''{
          nixberry.backup = {
            enable = ''' + str(enabled).lower() + ''';
            applications.web = { paths = [ "/var/lib/web" ]; stopUnits = [ "writer.service" ]; };
            restore.prepare = ''' + str(prepared).lower() + ''';
            restic = { enable = true; repository = "/var/lib/test-repository"; ''' + restic + ''' };
          };
        }'''

    def test_all_six_platforms_publish_separate_policy_services_timers_and_restore_guards(self):
        module = self.settings('''keepLast = 7; maxAgeSeconds = 900;
          pruneSchedule = "daily"; checkSchedule = "hourly"; persistent = true;''')
        values = self.evaluate('''builtins.mapAttrs (_: system: let
          c = (system.extendModules { modules = [ ''' + module + ''' ]; }).config;
          describe = name: { inherit (c.systemd.services.${name}) after restartIfChanged serviceConfig; };
        in {
          policy = c.system.build.nixberryBackupMaintenancePolicy;
          installed = builtins.fromJSON c.environment.etc."nixberry/maintenance.json".text;
          backupKeys = builtins.attrNames c.system.build.nixberryBackupPolicy;
          automatic = c.system.build.nixberryRestorePolicy.automaticUnits;
          prune = describe "nixberry-backup-prune";
          check = describe "nixberry-backup-check";
          pruneTimer = c.systemd.timers.nixberry-backup-prune.timerConfig;
          checkTimer = c.systemd.timers.nixberry-backup-check.timerConfig;
        }) f.nixberrySystems''')
        self.assertEqual(len(values), 6)
        for name, value in values.items():
            with self.subTest(target=name):
                expected = {"version": 1, "keepLast": 7, "maxAgeSeconds": 900}
                self.assertEqual(value["policy"], expected)
                self.assertEqual(value["installed"], expected)
                self.assertEqual(value["backupKeys"], sorted([
                    "version", "sourceId", "captureMethod", "capture", "repository", "passwordFile",
                    "repositoryIdFile", "recoveryFile", "runtimeDirectory", "timeoutSeconds", "ssh",
                ]))
                self.assertEqual(set(value["automatic"]), {
                    "nixberry-backup.service", "nixberry-backup-prune.service", "nixberry-backup-prune.timer",
                    "nixberry-backup-check.service", "nixberry-backup-check.timer",
                })
                for action in ("prune", "check"):
                    service = value[action]
                    self.assertIn("nixberry-backup.service", service["after"])
                    self.assertFalse(service["restartIfChanged"])
                    config = service["serviceConfig"]
                    self.assertEqual(config["User"], "root")
                    self.assertEqual(config["Group"], "root")
                    self.assertEqual(config["UMask"], "0077")
                    self.assertEqual(config["MemoryMax"], "1G")
                    self.assertEqual(config["MemorySwapMax"], 0)
                    self.assertTrue(config["PrivateTmp"])
                    self.assertTrue(config["NoNewPrivileges"])
                    self.assertNotIn("ExecStopPost", config)
                    self.assertGreaterEqual(config["TimeoutStartSec"], 3600)
                    self.assertIn("/run/nixberry-backup:", config["TemporaryFileSystem"][0])
                    timer = value[action + "Timer"]
                    self.assertEqual(timer["OnCalendar"], "daily" if action == "prune" else "hourly")
                    self.assertTrue(timer["Persistent"])
                    self.assertEqual(timer["Unit"], "nixberry-backup-" + action + ".service")
                self.assertIn("nixberry-capture-recovery.service", value["prune"]["after"])
                self.assertTrue(value["prune"]["serviceConfig"]["ExecStart"].endswith(" backup-prune --apply"))
                self.assertTrue(value["check"]["serviceConfig"]["ExecStart"].endswith(" backup-check --json"))

    def test_null_defaults_add_no_jobs_and_keep_existing_automatic_units(self):
        values = self.evaluate('''builtins.mapAttrs (_: system: let
          c = (system.extendModules { modules = [ ''' + self.settings() + ''' ]; }).config;
        in {
          maintenance = c.system.build.nixberryBackupMaintenancePolicy;
          services = builtins.filter (name: builtins.elem name [ "nixberry-backup-prune" "nixberry-backup-check" ])
            (builtins.attrNames c.systemd.services);
          timers = builtins.filter (name: builtins.elem name [ "nixberry-backup-prune" "nixberry-backup-check" ])
            (builtins.attrNames c.systemd.timers);
          automatic = c.system.build.nixberryRestorePolicy.automaticUnits;
        }) f.nixberrySystems''')
        for value in values.values():
            self.assertEqual(value["maintenance"], {"version": 1, "keepLast": None, "maxAgeSeconds": None})
            self.assertEqual(value["services"], [])
            self.assertEqual(value["timers"], [])
            self.assertEqual(value["automatic"], ["nixberry-backup.service"])

    def test_thresholds_enable_only_their_own_manual_job_and_restore_guard(self):
        for option, action in (("keepLast = 1;", "prune"), ("maxAgeSeconds = 1;", "check")):
            value = self.evaluate('''let c = (f.nixberrySystems.vm-demo-libvirt.extendModules {
              modules = [ ''' + self.settings(option) + ''' ]; }).config; in {
              services = builtins.filter (name: builtins.elem name [ "nixberry-backup-prune" "nixberry-backup-check" ])
                (builtins.attrNames c.systemd.services);
              timers = builtins.filter (name: builtins.elem name [ "nixberry-backup-prune" "nixberry-backup-check" ])
                (builtins.attrNames c.systemd.timers);
              automatic = c.system.build.nixberryRestorePolicy.automaticUnits;
            }''')
            self.assertEqual(value["services"], ["nixberry-backup-" + action])
            self.assertEqual(value["timers"], [])
            self.assertEqual(value["automatic"], ["nixberry-backup.service", "nixberry-backup-" + action + ".service"])

    def test_disabled_backups_install_no_maintenance_policy_or_jobs(self):
        value = self.evaluate('''let c = (f.nixberrySystems.vm-demo-libvirt.extendModules {
          modules = [ ''' + self.settings("keepLast = 7; maxAgeSeconds = 900;", enabled=False, prepared=False) + ''' ];
        }).config; in {
          policy = c.system.build ? nixberryBackupMaintenancePolicy;
          file = c.environment.etc ? "nixberry/maintenance.json";
          jobs = builtins.filter (name: builtins.elem name [ "nixberry-backup-prune" "nixberry-backup-check" ])
            (builtins.attrNames c.systemd.services);
        }''')
        self.assertEqual(value, {"policy": False, "file": False, "jobs": []})

    def test_invalid_thresholds_and_schedules_are_rejected(self):
        for setting in ("keepLast = 0;", "keepLast = 100001;", "keepLast = true;",
                        "maxAgeSeconds = 0;", "maxAgeSeconds = 31536001;", "maxAgeSeconds = 1.5;",
                        'keepLast = 1; pruneSchedule = "";', 'maxAgeSeconds = 1; checkSchedule = "";'):
            expression = '''let c = (f.nixberrySystems.vm-demo-libvirt.extendModules {
              modules = [ ''' + self.settings(setting) + ''' ]; }).config; in
              (builtins.tryEval (builtins.deepSeq c.nixberry.backup.restic true)).success'''
            with self.subTest(setting=setting):
                self.assertFalse(self.evaluate(expression))
        for setting, message in (
                ('pruneSchedule = "daily";', "Restic pruneSchedule requires keepLast."),
                ('checkSchedule = "hourly";', "Restic checkSchedule requires maxAgeSeconds.")):
            value = self.evaluate('''let c = (f.nixberrySystems.vm-demo-libvirt.extendModules {
              modules = [ ''' + self.settings(setting) + ''' ]; }).config; in
              map (item: item.message) (builtins.filter (item: !item.assertion) c.assertions)''')
            self.assertIn(message, value)
