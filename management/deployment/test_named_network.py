"""Named physical networks and shared NIC facts in isolated, fictional recipes."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check_inventory import evaluate, nix
from exporter import export
import runtime
import security
from test_support import copy_test_recipe


BACKENDS = ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware")
NETWORK = {
    "primary": "lan0", "dns": ["10.234.0.1", "2001:db8::53"],
    "interfaces": {
        "lan0": {"mac": "02:AA:00:00:00:10", "address": "10.123.0.1/24"},
        "wan0": {"mac": "02:AA:00:00:00:11", "address": "10.234.0.20/24", "gateway": "10.234.0.1"},
    },
}


class NamedNetworkChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="nixberry-named-network-")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.recipe = cls.root / "recipe"
        copy_test_recipe(Path(__file__).resolve().parents[2], cls.recipe)
        cls.normalize = cls.recipe / "management/rpi/normalize-target.nix"
        cls.network_helper = cls.recipe / "management/rpi/network.nix"
        cls.named = {
            "hostName": "rpi-named", "hardwareSerial": "0000000000000041",
            "installerAddress": "10.123.0.99", "network": copy.deepcopy(NETWORK),
            "diskDevice": "/dev/disk/by-id/usb-NAMED_FIXTURE", "diskSerial": "NAMED_FIXTURE", "rootGiB": 32,
        }
        cls.locked = copy.deepcopy(cls.named)
        cls.locked.update(hostName="rpi-locked", hardwareSerial="0000000000000042")
        cls.locked["network"]["interfaces"]["lan0"].update(mac="02:aa:00:00:00:20", address="10.123.0.2/24")
        cls.locked["network"]["interfaces"]["wan0"].update(mac="02:aa:00:00:00:21", address="10.234.0.21/24")
        public = (cls.recipe / "admin.pub").read_text().strip()
        cls.locked["security"] = {"mode": "luks", "interface": "wan0", "initrdHostKey": public, "recoveryHostKey": public}
        cls.staged = copy.deepcopy(cls.locked)
        cls.staged.update(hostName="rpi-staged", hardwareSerial="0000000000000044", installerAddress="10.123.0.97")
        cls.staged["network"].update(primary="wan0", recoveryInterface="lan0", dns=["10.40.50.1"])
        cls.staged["network"]["interfaces"]["lan0"].update(mac="02:aa:00:00:00:40", address="192.168.79.1/24")
        cls.staged["network"]["interfaces"]["wan0"].update(mac="02:aa:00:00:00:41", address="10.40.50.25/24", gateway="10.40.50.1")
        cls.early_lan = copy.deepcopy(cls.staged)
        cls.early_lan.update(hostName="rpi-early-lan", hardwareSerial="0000000000000046",
                             installerAddress="10.123.0.96", rootLabel="early-root", bootLabel="early-boot")
        cls.early_lan["network"]["interfaces"]["lan0"]["mac"] = "02:aa:00:00:00:50"
        cls.early_lan["network"]["interfaces"]["wan0"]["mac"] = "02:aa:00:00:00:51"
        cls.early_lan["security"].update(interface="lan0", address="192.168.79.59/24")
        cls.legacy = {
            "hostName": "rpi-legacy", "hardwareSerial": "0000000000000043", "installerAddress": "10.123.0.98",
            "address": "10.123.0.30/24", "gateway": "10.123.0.1", "dns": ["10.123.0.1"],
            "lanMac": "02:aa:00:00:00:30", "reserveMac": "02:aa:00:00:00:31",
            "diskDevice": "/dev/disk/by-id/usb-LEGACY_FIXTURE", "diskSerial": "LEGACY_FIXTURE", "rootGiB": 32,
        }
        for name, target in [("rpi-named", cls.named), ("rpi-locked", cls.locked),
                             ("rpi-staged", cls.staged), ("rpi-early-lan", cls.early_lan),
                             ("rpi-legacy", cls.legacy)]:
            text = nix(target)[:-1] + " adminKeyFile = ../../../admin.pub; }"
            (cls.recipe / "management/rpi/targets" / (name + ".nix")).write_text(text)

    def normalize_target(self, target):
        return evaluate("import " + str(self.normalize) + " " + nix(target))

    def test_named_primary_management_is_distinct_from_default_gateway_owner(self):
        value = self.normalize_target(self.named)
        self.assertEqual(value["address"], "10.123.0.1/24")
        self.assertEqual(value["lanMac"], "02:aa:00:00:00:10")
        self.assertEqual(value["gateway"], "")
        self.assertEqual(value["dns"], NETWORK["dns"])
        self.assertIsNone(value["reserveMac"])
        self.assertEqual(value["network"]["recoveryInterface"], "lan0")
        lan, wan = value["network"]["interfaces"]["lan0"], value["network"]["interfaces"]["wan0"]
        self.assertEqual((lan["mode"], lan["dns"], lan["gateway"]), ("static", [], ""))
        self.assertEqual((wan["gateway"], wan["dns"]), ("10.234.0.1", NETWORK["dns"]))
        isolated = copy.deepcopy(NETWORK)
        del isolated["interfaces"]["wan0"]["gateway"]
        isolated["interfaces"]["lan0"]["dns"] = ["127.0.0.1"]
        normalized = evaluate("import " + str(self.network_helper) + " { network = " + nix(isolated) + "; }")
        self.assertTrue(all(not row["gateway"] for row in normalized["interfaces"].values()))
        self.assertEqual(normalized["interfaces"]["lan0"]["dns"], ["127.0.0.1"])
        self.assertEqual(self.normalize_target(self.legacy), self.legacy)

    def test_invalid_or_ambiguous_named_declarations_fail_before_system_evaluation(self):
        cases = []
        for field in ["address", "gateway", "dns", "lanMac", "reserveMac"]:
            cases.append({**copy.deepcopy(self.named), field: self.legacy[field]})
        for mutation in [
            lambda n: n.update(primary="missing"),
            lambda n: n.update(recoveryInterface="missing"),
            lambda n: n.update(dns=[]),
            lambda n: n.update(dns=["invalid-dns"]),
            lambda n: n["interfaces"]["lan0"].update(mode="dhcp"),
            lambda n: n["interfaces"]["lan0"].update(address="10.123.0.1"),
            lambda n: n["interfaces"]["lan0"].update(mac="01:00:00:00:00:10"),
            lambda n: n["interfaces"]["lan0"].update(mac="00:00:00:00:00:00"),
            lambda n: n["interfaces"]["wan0"].update(mac=n["interfaces"]["lan0"]["mac"].lower()),
            lambda n: n["interfaces"]["wan0"].update(address=n["interfaces"]["lan0"]["address"]),
            lambda n: n["interfaces"]["lan0"].update(gateway="10.123.0.254"),
            lambda n: n["interfaces"].update(lo={"mac": "02:00:00:00:00:12", "address": "10.123.0.12/24"}),
        ]:
            target = copy.deepcopy(self.named)
            mutation(target["network"])
            cases.append(target)
        cases += [{**copy.deepcopy(self.named), "security": {"mode": "luks", "interface": "missing"}},
                  {**copy.deepcopy(self.legacy), "security": {"mode": "luks", "interface": "wan0"}}]
        results = evaluate("let normalize = import " + str(self.normalize) + "; in map (target: "
                           "builtins.tryEval (builtins.deepSeq (normalize target) true)) " + nix(cases))
        self.assertTrue(all(not row["success"] for row in results), results)

    def test_all_vm_backends_expose_same_facts_without_changing_network_configuration(self):
        cases = [{"backend": backend, "named": named} for backend in BACKENDS for named in [True, False]]
        values = evaluate('''let
          f = builtins.getFlake ''' + nix("path:" + str(self.recipe)) + ''';
          lib = f.inputs.nixos-raspberrypi.inputs.nixpkgs.lib;
          describe = case: let
            profile = if builtins.elem case.backend [ "libvirt" "proxmox" ] then "qemu" else case.backend;
            evaluated = lib.evalModules { modules = [
              ''' + str(self.recipe / "platforms/vm/system/networking.nix") + '''
              (''' + str(self.recipe / "platforms/vm/profiles") + ''' + "/${profile}.nix")
              ({ lib, ... }: {
                options.assertions = lib.mkOption { type = lib.types.listOf lib.types.attrs; default = [ ]; };
                options.systemd = lib.mkOption { type = lib.types.attrs; default = { }; };
                options.networking = lib.mkOption { type = lib.types.attrs; default = { }; };
                config = { _module.check = false;
                  nixberry.vm.adapters = lib.optionals case.named [
                    { name = "wan0"; mac = "02:00:00:00:00:01"; }
                    { name = "lan0"; mac = "02:00:00:00:00:02"; }
                  ];
                  nixberry.vm.network = { mode = "static"; address = "10.234.0.20/24";
                    gateway = "10.234.0.1"; dns = [ "10.234.0.1" ];
                    interfaces = lib.optionalAttrs case.named { lan0 = { mode = "static"; address = "10.123.0.1/24"; }; };
                  };
                };
              })
            ]; };
          in { facts = evaluated.config.nixberry.network;
            networks = evaluated.config.systemd.network.networks;
          };
        in map describe ''' + nix(cases))
        for case, value in zip(cases, values, strict=True):
            with self.subTest(case=case):
                facts = value["facts"]
                self.assertEqual(value["networks"]["10-lan"]["networkConfig"]["Gateway"], "10.234.0.1")
                if case["named"]:
                    self.assertEqual(facts["primaryInterface"], "wan0")
                    self.assertEqual(facts["interfaces"]["lan0"]["address"], "10.123.0.1/24")
                    self.assertEqual(facts["interfaces"]["lan0"]["gateway"], "")
                    self.assertEqual(facts["networkdUnits"]["wan0"], "10-lan")
                    self.assertEqual(facts["networkdUnits"]["lan0"], "20-nixberry-lan0")
                else:
                    self.assertIsNone(facts["primaryInterface"])
                    self.assertEqual(facts["interfaces"], {})

    def test_named_rpi_boot_inventory_recovery_and_initrd_keep_roles_and_secrets_separate(self):
        values = evaluate('''let f = builtins.getFlake ''' + nix("path:" + str(self.recipe)) + ''';
          c = f.nixberrySystems.rpi-locked.config;
          legacy = f.nixberrySystems.rpi-legacy.config;
          recovery = f.nixosConfigurations.nixberry-rpi-installer.config;
          staged = f.nixberrySystems.rpi-staged.config;
          ram = f.nixberryRpiRecoveries.rpi-staged.config;
          networks = values: builtins.mapAttrs (_: value: {
            inherit (value) matchConfig address gateway dns linkConfig networkConfig;
          }) values;
          manifests = builtins.fromJSON (builtins.unsafeDiscardStringContext
            recovery.environment.etc."nixberry-rpi-targets.json".text);
        in {
          system = c.system.build.toplevel.drvPath;
          facts = c.nixberry.network;
          normal = networks c.systemd.network.networks;
          early = networks c.boot.initrd.systemd.network.networks;
          root = c.fileSystems."/".device;
          hostKeys = c.boot.initrd.network.ssh.hostKeys;
          authorized = c.boot.initrd.network.ssh.authorizedKeys;
          supportsSecrets = c.boot.loader.supportsInitrdSecrets;
          inventory = f.nixberryDeployment.targets.rpi-locked;
          manifest = manifests.rpi-locked;
          recovery = networks recovery.systemd.network.networks;
          stagedFacts = staged.nixberry.network;
          stagedNetworks = networks staged.systemd.network.networks;
          stagedManifest = manifests.rpi-staged;
          ramFacts = ram.nixberry.network;
          ramNetworks = networks ram.systemd.network.networks;
          legacyFacts = legacy.nixberry.network;
          legacyNetworks = networks legacy.systemd.network.networks;
        }''')
        self.assertEqual(values["facts"]["primaryInterface"], "lan0")
        self.assertEqual(values["normal"]["10-nixberry-lan0"]["gateway"], [])
        self.assertEqual(values["normal"]["10-nixberry-wan0"]["gateway"], ["10.234.0.1"])
        for name in ["lan0", "wan0"]:
            row = values["normal"]["10-nixberry-" + name]
            self.assertEqual(row["networkConfig"].get("ConfigureWithoutCarrier", False), name == "lan0")
            self.assertEqual(row["linkConfig"]["RequiredForOnline"], "yes")
        for name, required in [("lan0", "no"), ("wan0", "yes")]:
            row = values["stagedNetworks"]["10-nixberry-" + name]
            self.assertEqual(row["networkConfig"].get("ConfigureWithoutCarrier", False), name == "lan0")
            self.assertEqual(row["linkConfig"]["RequiredForOnline"], required)
        early = list(values["early"].values())
        self.assertEqual(len(early), 1)
        self.assertEqual(early[0]["matchConfig"]["MACAddress"], "02:aa:00:00:00:21")
        self.assertEqual(early[0]["address"], ["10.234.0.21/24"])
        self.assertEqual(early[0]["gateway"], ["10.234.0.1"])
        self.assertTrue(values["supportsSecrets"])
        self.assertEqual(values["root"], "/dev/mapper/nixberry-root")
        self.assertEqual(values["hostKeys"], ["/etc/secrets/initrd/ssh_host_ed25519_key"])
        self.assertTrue(all(value.startswith('restrict,pty,command="systemctl default" ') for value in values["authorized"]))
        for projection in [values["inventory"]["spec"], values["manifest"]]:
            self.assertEqual(projection["address"], "10.123.0.2/24")
            self.assertEqual(projection["lanMac"], "02:aa:00:00:00:20")
            self.assertEqual(projection["gateway"], "")
            self.assertEqual(projection["network"]["interfaces"]["wan0"]["gateway"], "10.234.0.1")
        static_recovery = {row["matchConfig"].get("MACAddress"): row for row in values["recovery"].values()
                           if row.get("matchConfig", {}).get("MACAddress")}
        for mapping in [static_recovery,
                        {row["matchConfig"].get("MACAddress"): row for row in values["ramNetworks"].values()
                         if row.get("matchConfig", {}).get("MACAddress")}]:
            lan, wan = mapping["02:aa:00:00:00:40"], mapping["02:aa:00:00:00:41"]
            self.assertIn(lan["networkConfig"]["DHCP"], ["yes", "ipv4"])
            self.assertEqual(wan["networkConfig"]["DHCP"], "no")
            self.assertEqual(lan["linkConfig"]["RequiredForOnline"], "yes")
            self.assertEqual(wan["linkConfig"]["RequiredForOnline"], "no")
            for row in [lan, wan]:
                self.assertEqual((row["address"], row["gateway"], row["dns"]), ([], [], []))
                self.assertFalse(row["networkConfig"]["IPv6AcceptRA"])
                self.assertFalse(row["networkConfig"].get("ConfigureWithoutCarrier", False))
            self.assertEqual(wan["networkConfig"]["LinkLocalAddressing"], "no")
        self.assertEqual(values["stagedFacts"]["primaryInterface"], "wan0")
        self.assertEqual(values["stagedFacts"]["interfaces"]["lan0"]["address"], "192.168.79.1/24")
        self.assertEqual(values["stagedManifest"]["address"], "10.40.50.25/24")
        self.assertEqual(values["stagedManifest"]["network"]["recoveryInterface"], "lan0")
        self.assertEqual(values["ramFacts"]["primaryInterface"], "lan0")
        self.assertEqual(values["ramFacts"]["interfaces"]["lan0"]["mode"], "dhcp")
        self.assertEqual(values["ramFacts"]["interfaces"]["wan0"]["mode"], "disabled")
        self.assertEqual(values["legacyFacts"]["primaryInterface"], "enx02aa00000030")
        self.assertEqual(values["legacyFacts"]["interfaces"]["enx02aa00000031"]["mode"], "disabled")
        self.assertIn("10-lan", values["legacyNetworks"])
        self.assertIn("20-reserve", values["legacyNetworks"])
        self.locked_target = values["inventory"]

    def test_runtime_and_unlock_use_their_selected_interface_without_live_connections(self):
        value = self.normalize_target(self.locked)
        target = {"backend": "rpi", "spec": value, "security": self.locked["security"]}
        links = [{"ifname": name, "address": row["mac"],
                  "addr_info": [{"family": "inet", "local": row["address"].split("/")[0], "prefixlen": 24}]}
                 for name, row in value["network"]["interfaces"].items()]
        result = "aarch64\n" + value["hardwareSerial"] + "\nrpi-locked\n" + json.dumps(links) + "\n/dev/mapper/nixberry-root"
        with patch.object(runtime, "read", return_value=result) as read:
            snapshot = runtime.status(target)
        self.assertEqual(snapshot["addresses"], ["10.123.0.2"])
        self.assertIn("root@10.123.0.2", read.call_args.args[0])
        self.assertIn("ip -j address", read.call_args.args[0][-1])
        wrong_prefix = copy.deepcopy(links)
        wrong_prefix[1]["addr_info"][0]["prefixlen"] = 25
        for changed in [links[:1], wrong_prefix,
                        [{**row, "ifname": "wrong"} if row["ifname"] == "wan0" else row for row in links]]:
            bad = "aarch64\n" + value["hardwareSerial"] + "\nrpi-locked\n" + json.dumps(changed) + "\n/dev/mapper/nixberry-root"
            with patch.object(runtime, "read", return_value=bad), self.assertRaises(RuntimeError):
                runtime.status(target)
        with patch("security.subprocess.run") as run:
            preview = security.unlock(target, dry_run=True)
        run.assert_not_called()
        self.assertEqual(preview["address"], "10.234.0.21")
        self.assertEqual(preview["initrdHostKey"], self.locked["security"]["initrdHostKey"])
        legacy = {"backend": "rpi", "spec": self.legacy, "security": self.locked["security"] | {"interface": None}}
        self.assertIsNone(security.default_unlock_address(legacy))

    def test_initrd_address_override_preserves_installed_and_recovery_networks(self):
        # Catch using the installed router address in initrd, where it would
        # conflict with the still-running gateway during a staged replacement.
        accepted = evaluate('''let f = builtins.getFlake ''' + nix("path:" + str(self.recipe)) + ''';
          in builtins.tryEval f.nixberrySystems.rpi-early-lan.config.system.build.toplevel.drvPath''')
        self.assertTrue(accepted["success"], "A distinct same-subnet RPi initrd address must be accepted.")
        values = evaluate('''let f = builtins.getFlake ''' + nix("path:" + str(self.recipe)) + ''';
          c = f.nixberrySystems.rpi-early-lan.config;
          ram = f.nixberryRpiRecoveries.rpi-early-lan.config;
          sd = f.nixosConfigurations.nixberry-rpi-installer.config;
          networks = values: builtins.mapAttrs (_: value: {
            inherit (value) matchConfig address gateway dns networkConfig;
          }) values;
          manifests = builtins.fromJSON (builtins.unsafeDiscardStringContext
            sd.environment.etc."nixberry-rpi-targets.json".text);
        in {
          facts = c.nixberry.network;
          normal = networks c.systemd.network.networks;
          early = networks c.boot.initrd.systemd.network.networks;
          ram = networks ram.systemd.network.networks;
          sd = networks sd.systemd.network.networks;
          manifest = manifests.rpi-early-lan;
        }''')
        self.assertEqual(values["facts"]["primaryInterface"], "wan0")
        self.assertEqual(values["facts"]["interfaces"]["lan0"]["address"], "192.168.79.1/24")
        self.assertEqual(values["normal"]["10-nixberry-wan0"]["address"], ["10.40.50.25/24"])
        early = list(values["early"].values())
        self.assertEqual(len(early), 1)
        self.assertEqual(early[0]["matchConfig"]["MACAddress"], "02:aa:00:00:00:50")
        self.assertEqual((early[0]["address"], early[0]["gateway"], early[0]["dns"]),
                         (["192.168.79.59/24"], [], []))
        for recovery in [values["ram"], values["sd"]]:
            by_mac = {row["matchConfig"].get("MACAddress"): row for row in recovery.values()}
            lan, wan = by_mac["02:aa:00:00:00:50"], by_mac["02:aa:00:00:00:51"]
            self.assertEqual(lan["networkConfig"]["DHCP"], "ipv4")
            self.assertEqual(wan["networkConfig"]["DHCP"], "no")
            for row in [lan, wan]:
                self.assertEqual((row["address"], row["gateway"], row["dns"]), ([], [], []))
        self.assertEqual(values["manifest"]["address"], "10.40.50.25/24")
        self.assertEqual(values["manifest"]["security"]["address"], "192.168.79.59/24")

    def test_initrd_address_override_rejects_a_different_subnet_or_prefix(self):
        cases = []
        for address in ["192.168.79.59/25", "192.168.80.59/24", "192.168.79.59"]:
            target = copy.deepcopy(self.early_lan)
            target["security"]["address"] = address
            cases.append(target)
        cases.append({**copy.deepcopy(self.legacy),
                      "security": {"mode": "luks", "address": "10.123.0.59/24"}})
        rejected = evaluate("let normalize = import " + str(self.normalize) + "; in map (target: "
                            "builtins.tryEval (builtins.deepSeq (normalize target) true)) " + nix(cases))
        self.assertTrue(all(not value["success"] for value in rejected), rejected)

    def test_null_initrd_address_preserves_legacy_and_named_policy(self):
        values = evaluate("let policy = import " + str(self.recipe / "management/rpi/security.nix")
                          + "; in map (security: { absent = policy { inherit security; }; "
                          "explicitNull = policy { security = security // { address = null; }; }; }) "
                          + nix([{"mode": "luks"}, self.locked["security"]]))
        for value in values:
            self.assertEqual(value["absent"], value["explicitNull"])
            self.assertNotIn("address", value["absent"])

    def test_named_network_cannot_bypass_the_protected_installation_address(self):
        target = copy.deepcopy(self.named)
        target.update(hostName="rpi-protected", hardwareSerial="0000000000000045", installerAddress="192.168.79.1",
                      rootLabel="guard-root", bootLabel="guard-boot")
        path = self.recipe / "management/rpi/targets/rpi-protected.nix"
        path.write_text(nix(target)[:-1] + " adminKeyFile = ../../../admin.pub; }")
        try:
            result = subprocess.run(["nix", "eval", "--raw", "--no-write-lock-file",
                                     "path:" + str(self.recipe) + "#nixberrySystems.rpi-protected.config.system.build.toplevel.drvPath"],
                                    capture_output=True, text=True, timeout=120)
        finally:
            path.unlink()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected router", result.stderr)

    def test_offline_rpi_manifest_retains_all_nics_and_public_unlock_selection(self):
        normalized = self.normalize_target(self.locked)
        target = {"backend": "rpi", "profile": "rpi-locked", "configuration": "default",
                  "spec": {key: value for key, value in normalized.items() if key != "security"},
                  "security": self.locked["security"]}
        with tempfile.TemporaryDirectory(prefix="nixberry-rpi-export-") as temporary:
            root = Path(temporary)
            recipe = root / "recipe"
            recipe.mkdir()
            (recipe / "flake.nix").write_text("{}")
            (recipe / "flake.lock").write_text("{}")
            (recipe / "README.md").write_text("Fictional recovery media fixture.\n")
            snapshot = root / "snapshot"
            shutil.copytree(recipe, snapshot)
            image = root / "image"
            image.mkdir()
            (image / "recovery.img.zst").write_bytes(b"fictional prebuilt recovery media")

            def output(command, **kwargs):
                if command[1:3] == ["flake", "metadata"]:
                    return json.dumps({"path": str(snapshot)})
                if "#nixberryDeployment.targets." in command[-1]:
                    return json.dumps(target)
                return "/nix/store/fictional-rpi-system"

            with patch("exporter.subprocess.run", return_value=SimpleNamespace(stdout=str(image))), \
                    patch("exporter.subprocess.check_output", side_effect=output):
                result = export(recipe, "rpi-locked", target, root / "bundle")
            manifest = json.loads((root / "bundle/manifest.json").read_text())
            self.assertEqual({key: value for key, value in result.items() if key != "destination"}, manifest)
            self.assertEqual(manifest["spec"]["network"], normalized["network"])
            self.assertEqual(manifest["spec"]["address"], "10.123.0.2/24")
            self.assertEqual(manifest["security"]["interface"], "wan0")
            self.assertEqual(manifest["security"]["initrdHostKey"], self.locked["security"]["initrdHostKey"])


if __name__ == "__main__":
    unittest.main()
