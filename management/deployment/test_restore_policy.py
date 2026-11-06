"""Restore preparation is shared by every platform and keeps capture v1 stable."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import restore_target

from test_support import copy_test_recipe
from scaffold import new_vm
from check_inventory import nix


class RestorePolicyTests(unittest.TestCase):
    def test_all_platforms_project_restore_units_without_changing_capture_app_schema(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / 'recipe'
            copy_test_recipe(root, recipe)
            (recipe / 'configurations/prepared.nix').write_text('''{ ... }: {
              imports=[ ../modules/common.nix ];
              nixberry.backup={ enable=true; restore.prepare=true;
                applications.web={ paths=["/var/lib/web"]; stopUnits=["writer.service"]; };
                applications.static={ paths=["/var/lib/statická"]; restoreUnits=[]; };
                applications.dns={ paths=["/var/lib/dns"]; restoreUnits=["dnsmasq.service"]; };
              };
              systemd.services.writer.script="sleep infinity";
            }''')
            for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
                new_vm(recipe, {}, {}, 'vm-demo-' + backend, backend, offline=True, configuration='prepared')
            rpi = (root / 'examples/rpi.nix').read_text()
            (recipe / 'machines/rpi/rpi-demo.nix').write_text(rpi.rstrip().removesuffix('}') + ' configuration="prepared"; }\n')
            expression = ('let f=builtins.getFlake ' + nix('path:' + str(recipe)) + '; in builtins.mapAttrs (_: s: {'
                ' restore=s.config.system.build.nixberryRestorePolicy;'
                ' hash=builtins.hashString "sha256" (builtins.toJSON s.config.system.build.nixberryRestorePolicy);'
                ' capture=s.config.system.build.nixberryCapturePolicy.applications;'
                '}) f.nixberrySystems')
            result = subprocess.run(['nix','eval','--impure','--json','--expr',expression], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = json.loads(result.stdout)
            self.assertEqual(len(values), 6)
            for name, settings in values.items():
                with self.subTest(target=name):
                    self.assertEqual(settings['restore']['applications']['web']['units'], ['writer.service'])
                    self.assertEqual(settings['restore']['applications']['static']['units'], [])
                    self.assertEqual(settings['restore']['applications']['dns']['units'], ['dnsmasq.service'])
                    self.assertEqual(restore_target.policy_hash(settings['restore']), settings['hash'])
                    self.assertEqual(set(settings['capture']['web']), {'paths','stopUnits'})
                    self.assertEqual(settings['restore']['architecture'], 'aarch64-linux' if name=='rpi-demo' else 'x86_64-linux')
            for extra, message in (
                    ('stopUnits=[];', 'restoreUnits explicitly'),
                    ('stopUnits=["writer.service"]; restoreUnits=["sshd.socket"];', 'networking or management'),
                    ('stopUnits=["writer.service"]; restoreUnits=["nixberry-update.service"];', 'networking or management'),
                    ('stopUnits=["writer.service"];', 'outside application data')):
                state = 'stateDirectory="/var/lib/web/restore";' if message == 'outside application data' else ''
                (recipe / 'configurations/prepared.nix').write_text('''{ ... }: {
                  imports=[ ../modules/common.nix ]; nixberry.backup={ enable=true;
                  restore={ prepare=true; ''' + state + ''' };
                  applications.web={ paths=["/var/lib/web"]; ''' + extra + ''' }; }; }''')
                failed = subprocess.run(['nix','eval','--impure','--json','--expr',expression], text=True, capture_output=True)
                with self.subTest(rejected=extra, reason=message):
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn(message, failed.stderr)
