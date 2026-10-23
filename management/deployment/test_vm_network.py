"""Evaluate DHCP and static VM networking independently of private declarations."""

from pathlib import Path
import tempfile
import unittest

from check_inventory import evaluate, nix
from test_support import copy_test_recipe


class VmNetworkChecks(unittest.TestCase):
    def test_static_network_and_default_dhcp_use_shared_module(self):
        temporary = tempfile.TemporaryDirectory(prefix="nixberry-network-test-")
        self.addCleanup(temporary.cleanup)
        recipe = Path(temporary.name) / "recipe"
        copy_test_recipe(Path(__file__).resolve().parents[2], recipe)
        result = evaluate('''let
          f = builtins.getFlake ''' + nix("path:" + str(recipe)) + ''';
          fixed = {
            mode = "static"; address = "10.0.1.19/24";
            gateway = "10.0.1.1"; dns = [ "10.0.1.1" ];
          };
          describe = system: {
            network = {
              inherit (system.config.systemd.network.networks."10-lan") networkConfig matchConfig;
            };
            dns = system.config.networking.nameservers;
          };
          staticSystem = backend: f.nixosConfigurations.${backend}.extendModules {
            modules = [ { nixberry.vm.network = fixed; } ];
          };
          profiles = [ "nixberry-vm" "nixberry-proxmox" "nixberry-hyperv"
                       "nixberry-virtualbox" "nixberry-vmware" ];
        in {
          dhcp = builtins.listToAttrs (map (name: {
            inherit name; value = describe f.nixosConfigurations.${name};
          }) profiles);
          static = builtins.listToAttrs (map (name: {
            inherit name; value = describe (staticSystem name);
          }) profiles);
          incomplete = builtins.filter (item: !item.assertion) (f.nixosConfigurations.nixberry-proxmox.extendModules {
            modules = [ { nixberry.vm.network.mode = "static"; } ];
          }).config.assertions;
        }''')
        for profile, configured in result["static"].items():
            with self.subTest(profile=profile):
                dhcp = result["dhcp"][profile]["network"]["networkConfig"]
                self.assertEqual(dhcp["DHCP"], "ipv4")
                self.assertNotIn("Address", dhcp)
                static = configured["network"]["networkConfig"]
                self.assertEqual(static["DHCP"], "no")
                self.assertEqual(static["Address"], "10.0.1.19/24")
                self.assertEqual(static["Gateway"], "10.0.1.1")
                self.assertEqual(static["DNS"], ["10.0.1.1"])
                self.assertEqual(configured["dns"], ["10.0.1.1"])
                self.assertEqual(configured, result["static"]["nixberry-vm"])
                self.assertNotIn("MACAddress", configured["network"]["matchConfig"])
        self.assertTrue(any(not item["assertion"] and "Static VM networking" in item["message"]
                            for item in result["incomplete"]))


if __name__ == "__main__":
    unittest.main()
