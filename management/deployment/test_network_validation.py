"""Reject unusable static network declarations before any image or deployment."""

from pathlib import Path
import ipaddress
import tempfile
import unittest

from check_inventory import evaluate, nix
from test_support import copy_test_recipe


class NetworkValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="nixberry-network-validation-")
        cls.addClassCleanup(temporary.cleanup)
        cls.recipe = Path(temporary.name) / "recipe"
        copy_test_recipe(Path(__file__).resolve().parents[2], cls.recipe)

    def evaluate_modules(self, cases):
        recipe = self.recipe
        return evaluate('''let
          f = builtins.getFlake ''' + nix("path:" + str(recipe)) + ''';
          lib = f.inputs.nixos-raspberrypi.inputs.nixpkgs.lib;
          recipe = ''' + str(recipe) + ''';
          cases = ''' + nix(cases) + ''';
          describe = case: let
            vm = case.backend != "rpi";
            profile = if builtins.elem case.backend [ "libvirt" "proxmox" ]
              then "qemu" else case.backend;
            system = lib.evalModules {
              modules = [
                (if vm then recipe + "/platforms/vm/system/networking.nix"
                 else recipe + "/platforms/rpi/options.nix")
                ({ lib, ... }: {
                  options.assertions = lib.mkOption {
                    type = lib.types.listOf lib.types.attrs;
                    default = [ ];
                  };
                  config = {
                    _module.check = false;
                  } // (if vm then { nixberry.vm.network = case.network; }
                        else { nixberry.rpi = case.network; });
                })
              ] ++ lib.optional vm (recipe + "/platforms/vm/profiles/${profile}.nix");
            };
          in map (item: item.message) (builtins.filter (item: !item.assertion) system.config.assertions);
        in map describe cases''')

    def test_ip_syntax_matches_standard_parser_at_address_boundaries(self):
        candidates = [
            "0.0.0.0", "255.255.255.255", "10.0.1.1", "01.2.3.4", "256.0.0.1",
            "1.2.3", "1.2.3.4.5", "-1.0.0.1", "10.0.1.1/24", "10.0.1.1:53", "",
            "::", "::1", "2001:db8::1", "2001:DB8:0:0:0:0:0:1", "1:2:3:4:5:6:7:8",
            "::ffff:192.0.2.1", "1:2:3:4:5:6:192.0.2.1", "::192.0.2.1",
            ":", ":::1", "1::2::3", "1:2:3:4:5:6:7", "1:2:3:4:5:6:7:8:9",
            "1:2:3:4:5:6:7::8", "1:2:3:4:5:6:7:8::", "12345::1", "2001:db8::xyz",
            "192.0.2.1::", "::ffff:256.0.0.1", "2001:db8::1/64",
        ]
        expected = []
        for address in candidates:
            try:
                ipaddress.ip_address(address)
            except ValueError:
                expected.append(False)
            else:
                expected.append(True)
        result = evaluate('''let
          f = builtins.getFlake ''' + nix("path:" + str(self.recipe)) + ''';
          lib = f.inputs.nixos-raspberrypi.inputs.nixpkgs.lib;
          addresses = import ''' + str(self.recipe / "modules/system/networking/addresses.nix") + ''' { inherit lib; };
        in map addresses.ip ''' + nix(candidates))
        for address, actual, wanted in zip(candidates, result, expected, strict=True):
            with self.subTest(address=address):
                self.assertEqual(actual, wanted)

    def test_static_validation_for_all_vm_backends_and_unchanged_dhcp(self):
        valid = {"mode": "static", "address": "10.0.1.19/24", "gateway": "10.0.1.1",
                 "dns": ["10.0.1.1", "2001:db8::1", "::ffff:192.0.2.1"]}
        invalid = [
            ({"address": "not-an-address"}, "IPv4 address with a prefix"),
            ({"address": "10.0.1.19"}, "IPv4 address with a prefix"),
            ({"address": "10.0.1.19/33"}, "IPv4 address with a prefix"),
            ({"address": "256.0.1.19/24"}, "IPv4 address with a prefix"),
            ({"gateway": "999.0.1.1"}, "IPv4 gateway"),
            ({"gateway": "10.0.1.1/24"}, "IPv4 gateway"),
            ({"dns": ["not-a-dns-address"]}, "DNS servers"),
            ({"dns": ["2001:::1"]}, "DNS servers"),
            ({"dns": []}, "nonempty dns"),
        ]
        cases, expected = [], []
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            for changes, message in [({}, None), ({"mode": "dhcp", "address": "", "gateway": "", "dns": []}, None), *invalid]:
                cases.append({"backend": backend, "network": {**valid, **changes}})
                expected.append(message)
        for case, messages, message in zip(cases, self.evaluate_modules(cases), expected, strict=True):
            with self.subTest(case=case):
                if message:
                    self.assertTrue(any(message in item for item in messages), messages)
                else:
                    self.assertEqual(messages, [])

    def test_rpi_uses_the_same_ip_and_dns_validation(self):
        valid = {"hostName": "rpi-demo", "hardwareSerial": "0000000000000000",
                 "installerAddress": "192.168.50.20", "address": "192.168.50.20/24",
                 "gateway": "192.168.50.1", "dns": ["192.168.50.1", "2001:db8::1"],
                 "lanMac": "02:00:00:00:00:20", "diskDevice": "/dev/disk/by-id/ata-EXAMPLE_SERIAL",
                 "diskSerial": "EXAMPLE_SERIAL", "adminKeyFile": "/tmp/nixberry-network-validation.pub"}
        invalid = [
            ({"installerAddress": "not-an-address"}, "installer address"),
            ({"address": "192.168.50.20"}, "system address"),
            ({"address": "192.168.50.20/33"}, "system address"),
            ({"gateway": "300.1.1.1"}, "gateway"),
            ({"dns": ["bad"]}, "DNS servers"),
            ({"dns": ["2001:::1"]}, "DNS servers"),
            ({"dns": []}, "DNS servers"),
        ]
        cases = [{"backend": "rpi", "network": {**valid, **changes}}
                 for changes, _ in [({}, None), *invalid]]
        for case, messages, (_, message) in zip(cases, self.evaluate_modules(cases), [({}, None), *invalid], strict=True):
            with self.subTest(case=case):
                if message:
                    self.assertTrue(any(message in item for item in messages), messages)
                else:
                    self.assertEqual(messages, [])


class RecipeIsolationTests(unittest.TestCase):
    def test_linked_private_configurations_are_excluded_from_test_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "source"
            (recipe / "configurations/public").mkdir(parents=True)
            (recipe / "configurations/default.nix").write_text("{}")
            (recipe / "configurations/public/default.nix").write_text("{}")
            external = root / "private"
            external.mkdir()
            (external / "default.nix").write_text("invalid private Nix source")
            (recipe / "configurations/linked").symlink_to(external, target_is_directory=True)
            (recipe / "configurations/broken").symlink_to(root / "missing", target_is_directory=True)
            destination = root / "fixture"
            copy_test_recipe(recipe, destination)
            self.assertEqual(sorted(path.name for path in (destination / "configurations").iterdir()),
                             ["default.nix", "public"])
            self.assertTrue((destination / "configurations/public/default.nix").is_file())
            self.assertTrue((recipe / "configurations/linked").is_symlink())
            self.assertTrue((recipe / "configurations/broken").is_symlink())


if __name__ == "__main__":
    unittest.main()
