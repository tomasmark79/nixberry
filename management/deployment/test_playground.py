"""Router examples must reject overlapping LAN/WAN networks of any prefix."""

import json
from pathlib import Path
import subprocess
import unittest


class PlaygroundNetworkTests(unittest.TestCase):
    def test_lan_and_wan_must_not_overlap(self):
        recipe = Path(__file__).resolve().parents[2]
        for address, expected in [("192.168.79.2/24", True), ("192.168.79.2/16", False),
                                  ("192.168.192.130/25", False), ("10.1.2.3/8", True),
                                  ("10.1.2.3/0", False)]:
            with self.subTest(wan=address):
                expression = '''let
                  f = builtins.getFlake %s;
                  module = import %s/modules/services/router.nix {
                    lib = f.inputs.nixos-raspberrypi.inputs.nixpkgs.lib;
                    pkgs = {};
                    config.nixberry.network.interfaces = {
                      lan0 = { address = "192.168.192.1/24"; mode = "static"; gateway = ""; };
                      wan0 = { address = %s; mode = "static"; gateway = "192.168.79.1"; dns = ["1.1.1.1"]; };
                    };
                  };
                in builtins.all (a: a.assertion) module.assertions''' % (json.dumps("path:" + str(recipe)), recipe, json.dumps(address))
                result = subprocess.run(["nix", "eval", "--impure", "--json", "--expr", expression],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
