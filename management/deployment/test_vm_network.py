"""Skutečné vyhodnocení sítě VM: DHCP, pevná IP a nezávislost na hypervizoru."""

from pathlib import Path
import unittest

from check_inventory import evaluate, nix


class VmNetworkChecks(unittest.TestCase):
    def test_static_network_and_default_dhcp_use_shared_module(self):
        recipe = Path(__file__).resolve().parents[2]
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
        in {
          dhcp = describe f.nixosConfigurations.nixberry-proxmox;
          proxmox = describe (staticSystem "nixberry-proxmox");
          hyperv = describe (staticSystem "nixberry-hyperv");
          incomplete = builtins.filter (item: !item.assertion) (f.nixosConfigurations.nixberry-proxmox.extendModules {
            modules = [ { nixberry.vm.network.mode = "static"; } ];
          }).config.assertions;
        }''')
        self.assertEqual(result["dhcp"]["network"]["networkConfig"]["DHCP"], "ipv4")
        self.assertNotIn("Address", result["dhcp"]["network"]["networkConfig"])
        static = result["proxmox"]["network"]["networkConfig"]
        self.assertEqual(static["DHCP"], "no")
        self.assertEqual(static["Address"], "10.0.1.19/24")
        self.assertEqual(static["Gateway"], "10.0.1.1")
        self.assertEqual(static["DNS"], ["10.0.1.1"])
        self.assertEqual(result["proxmox"]["dns"], ["10.0.1.1"])
        self.assertEqual(result["proxmox"], result["hyperv"])
        self.assertNotIn("MACAddress", result["proxmox"]["network"]["matchConfig"])
        self.assertTrue(any(not item["assertion"] and "Static VM networking" in item["message"]
                            for item in result["incomplete"]))


if __name__ == "__main__":
    unittest.main()
