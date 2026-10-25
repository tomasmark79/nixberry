"""Physical root encryption is opt-in and cannot silently weaken its policy."""

from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from test_support import copy_test_recipe


class RpiSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='nixberry-rpi-security-')
        cls.addClassCleanup(temporary.cleanup)
        cls.recipe = Path(temporary.name) / 'recipe'
        copy_test_recipe(Path(__file__).parents[2], cls.recipe)
        for name, mode in [('rpi-demo', 'luks'), ('rpi-plain', 'none')]:
            (cls.recipe / 'management/rpi/targets' / (name + '.nix')).write_text('''{
              hostName = "''' + name + '''"; hardwareSerial = "0000000000000001";
              installerAddress = "192.168.50.20"; address = "192.168.50.20/24";
              gateway = "192.168.50.1"; dns = [ "192.168.50.1" ];
              lanMac = "02:00:00:00:00:01"; diskDevice = "/dev/disk/by-id/usb-TEST_DISK";
              diskSerial = "TEST_DISK"; rootGiB = 32; adminKeyFile = ../../../admin.pub;
              security.mode = "''' + mode + '''";
            }''')

    def evaluate(self, expression):
        result = subprocess.run(['nix', 'eval', '--impure', '--json', '--no-write-lock-file', '--expr',
            'let f = builtins.getFlake ' + json.dumps('path:' + str(self.recipe)) + '; in ' + expression],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_encrypted_and_default_root_boot_contracts(self):
        result = self.evaluate('''let describe = name: let c = f.nixberrySystems.${name}.config; in {
          system = c.system.build.toplevel.drvPath;
          root = c.fileSystems."/".device;
          secretSupport = c.boot.loader.supportsInitrdSecrets;
          earlySsh = c.boot.initrd.network.ssh.enable;
          keys = c.boot.initrd.network.ssh.authorizedKeys;
          port = c.boot.initrd.network.ssh.port;
          network = c.boot.initrd.systemd.network.networks."10-lan".matchConfig;
        }; in { encrypted = describe "rpi-demo"; plain = {
          system = f.nixberrySystems.rpi-plain.config.system.build.toplevel.drvPath;
          root = f.nixberrySystems.rpi-plain.config.fileSystems."/".device;
          secretSupport = f.nixberrySystems.rpi-plain.config.boot.loader.supportsInitrdSecrets;
          earlySsh = f.nixberrySystems.rpi-plain.config.boot.initrd.network.ssh.enable;
        }; }''')
        encrypted, plain = result['encrypted'], result['plain']
        self.assertEqual(encrypted['root'], '/dev/mapper/nixberry-root')
        self.assertTrue(encrypted['secretSupport'])
        self.assertTrue(encrypted['earlySsh'])
        self.assertEqual(encrypted['port'], 2222)
        self.assertEqual(encrypted['network']['MACAddress'], '02:00:00:00:00:01')
        self.assertTrue(encrypted['keys'])
        self.assertTrue(all(key.startswith('restrict,pty,command="systemctl default" ') for key in encrypted['keys']))
        self.assertNotEqual(plain['root'], '/dev/mapper/nixberry-root')
        self.assertFalse(plain['secretSupport'])
        self.assertFalse(plain['earlySsh'])

    def test_configuration_cannot_downgrade_security_or_enable_external_swap(self):
        module = self.recipe / 'configurations/default.nix'
        original = module.read_text()
        self.addCleanup(module.write_text, original)
        for override, message in [
            ('nixberry.rpi.security = lib.mkForce { mode = "none"; };', 'must preserve the RPi security policy'),
            ('swapDevices = lib.mkForce [ { device = "/dev/test-swap"; } ];', 'swap file inside the protected root'),
            ('boot.loader.supportsInitrdSecrets = lib.mkForce false;', 'secrets must be installed at runtime'),
            ('disko.devices.disk.ssd.device = lib.mkForce "/dev/test-other-disk";', 'declared physical disk'),
        ]:
            module.write_text('{ lib, ... }: { imports = [ ../modules/common.nix ]; ' + override + ' }')
            result = subprocess.run(['nix', 'eval', '--raw', '--no-write-lock-file',
                'path:' + str(self.recipe) + '#nixberrySystems.rpi-demo.config.system.build.toplevel.drvPath'],
                capture_output=True, text=True, timeout=90)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)

    def test_inventory_exposes_physical_unlock_and_keeps_vm_image_preparation_separate(self):
        result = self.evaluate('''{
          encrypted = f.nixberryDeployment.targets.rpi-demo;
          plain = f.nixberryDeployment.targets.rpi-plain;
        }''')
        self.assertEqual(result['encrypted']['security']['mode'], 'luks')
        self.assertNotIn('security', result['encrypted']['spec'])
        self.assertNotIn('security', result['plain'])

    def test_recovery_network_uses_the_encrypted_board_static_address(self):
        result = self.evaluate('''let c = f.nixosConfigurations.nixberry-rpi-installer.config; in {
          system = c.system.build.toplevel.drvPath;
          networkd = c.networking.useNetworkd;
          address = c.systemd.network.networks."00-nixberry-0000000000000001".address;
          match = c.systemd.network.networks."00-nixberry-0000000000000001".matchConfig;
          fallback = c.systemd.network.networks."90-recovery-dhcp".networkConfig.DHCP;
        }''')
        self.assertTrue(result['networkd'])
        self.assertEqual(result['address'], ['192.168.50.20/24'])
        self.assertEqual(result['match']['MACAddress'], '02:00:00:00:00:01')
        self.assertEqual(result['fallback'], 'yes')
