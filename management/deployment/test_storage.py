"""Root layouts preserve ext4 and provide a snapshot boundary for Btrfs."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from check_inventory import nix
from scaffold import new_vm
from test_support import copy_test_recipe
import updater
import runtime


ROOT = Path(__file__).resolve().parents[2]


def evaluate(arguments):
    return subprocess.run(
        ['nix', 'eval', '--impure', '--json', '--expr',
         '(import ' + str(ROOT / 'platforms/storage/root-content.nix') + ') ' + nix(arguments)],
        capture_output=True, text=True, timeout=20)


class StorageTests(unittest.TestCase):
    def test_scaffolding_selects_both_filesystems_for_every_vm_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary)
            (recipe / 'machines/vm').mkdir(parents=True)
            (recipe / 'machines/vm/default.nix').touch()
            (recipe / 'flake.nix').touch()
            (recipe / 'configurations').mkdir()
            (recipe / 'configurations/default.nix').write_text('{}')
            for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
                for filesystem in ('ext4', 'btrfs'):
                    for method in ('image', 'iso'):
                        name = 'vm-demo-' + backend + '-' + filesystem + '-' + method
                        path, _ = new_vm(recipe, {}, {}, name, backend, offline=True,
                                         filesystem=filesystem, method=method)
                        declared = json.loads(subprocess.check_output(
                            ['nix', 'eval', '--json', '--impure', '--expr', 'import ' + str(path)], text=True))
                        self.assertEqual(declared['rootFilesystem'], filesystem)
                        self.assertEqual(declared['deploymentMethod'], method)

    def test_update_checks_actual_root_format_before_build_or_activation(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware', 'rpi'):
            for filesystem in ('ext4', 'btrfs'):
                target = {'backend': backend, 'rootFilesystem': filesystem,
                          'spec': {'hardwareSerial': '0000000000000000', 'diskSerial': 'test',
                                   'hostName': 'rpi-demo', 'uuid': 'test', 'mac': '52:54:00:00:00:01'}}
                with self.subTest(backend=backend, filesystem=filesystem), \
                        patch('updater.connection', return_value=['ssh', 'test-only']), \
                        patch('updater.read', return_value='/nix/store/' + 'a' * 32 + '-nixos-system-test') as read:
                    updater.preflight(target, {})
                    command = read.call_args.args[0][-1]
                    self.assertIn('findmnt -n -o FSTYPE /', command)
                    self.assertIn('= ' + filesystem, command)

    def test_isolated_systems_use_the_selected_format_with_and_without_luks(self):
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / 'recipe'
            copy_test_recipe(ROOT, recipe)
            for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
                for filesystem in ('ext4', 'btrfs'):
                    for encrypted in (False, True):
                        name = 'vm-demo-' + backend + '-' + filesystem + ('-luks' if encrypted else '')
                        new_vm(recipe, {}, {}, name, backend, offline=True, filesystem=filesystem,
                               security_mode='luks' if encrypted else 'none')
            original = (recipe / 'examples/rpi.nix').read_text()
            for filesystem in ('ext4', 'btrfs'):
                for encrypted in (False, True):
                    name = 'rpi-demo-' + filesystem + ('-luks' if encrypted else '')
                    extra = 'rootFilesystem="' + filesystem + '";'
                    if encrypted:
                        extra += 'security.mode="luks";'
                    (recipe / 'machines/rpi' / (name + '.nix')).write_text(original.rsplit('}', 1)[0] + extra + '}')
            expression = '''let f = builtins.getFlake ''' + nix('path:' + str(recipe)) + '''; in
              builtins.mapAttrs (name: system: let c = system.config;
                disk = if c ? nixberry.rpi then c.disko.devices.disk.ssd else c.disko.devices.disk.system;
                root = disk.content.partitions.root.content;
              in { filesystem=c.fileSystems."/".fsType;
                declared=f.nixberryDeployment.targets.${name}.rootFilesystem;
                type=root.type; innerType=if root.type == "luks" then root.content.type else root.type;
                snapshots=c.fileSystems ? "/.snapshots";
                rootOptions=c.fileSystems."/".options;
                swap=map (s: s.device) c.swapDevices;
                localDiskTool=if c ? nixberry.rpi && root.type != "luks"
                  then f.nixberryRpiDiskTools.${name}.drvPath else null;
              }) f.nixberrySystems'''
            result = subprocess.run(['nix', 'eval', '--json', '--impure', '--no-write-lock-file', '--expr', expression],
                                    capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stderr[-6000:])
            systems = json.loads(result.stdout)
            self.assertEqual(len(systems), 24)
            for name, system in systems.items():
                filesystem = 'btrfs' if 'btrfs' in name else 'ext4'
                if system['swap']:
                    self.assertEqual(system['swap'], ['/swap/swapfile' if filesystem == 'btrfs' else '/swapfile'], name)
                self.assertEqual(system['filesystem'], filesystem, name)
                self.assertEqual(system['declared'], filesystem, name)
                self.assertEqual(system['snapshots'], filesystem == 'btrfs', name)
                self.assertEqual(system['type'], 'luks' if name.endswith('-luks') else
                                 'btrfs' if filesystem == 'btrfs' else 'filesystem', name)
                if filesystem == 'btrfs':
                    self.assertIn('subvol=@root', system['rootOptions'], name)
                    self.assertEqual(system['innerType'], 'btrfs', name)

    def test_format_guard_executes_and_refuses_other_filesystems(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware', 'rpi'):
            for expected in ('ext4', 'btrfs'):
                target = {'backend': backend, 'rootFilesystem': expected,
                          'spec': {'hardwareSerial': '0000000000000000', 'diskSerial': 'test',
                                   'hostName': 'rpi-demo', 'uuid': 'test', 'mac': '52:54:00:00:00:01'}}
                check = runtime.installation_checks(target)[0]
                for actual in ('ext4', 'btrfs', 'tmpfs'):
                    command = 'findmnt() { printf "%s\\n" ' + actual + '; }; ' + check
                    result = subprocess.run(['bash', '-c', command], capture_output=True)
                    self.assertEqual(result.returncode == 0, expected == actual,
                                     (backend, expected, actual))
                self.assertTrue(any('--nofsroot' in command for command in runtime.installation_checks(target)))

    def test_existing_ext4_layout_is_preserved(self):
        for arguments in ({}, {'filesystem': 'ext4'}, {'filesystem': 'ext4', 'label': 'demo-root'}):
            with self.subTest(arguments=arguments):
                result = evaluate(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = {'type': 'filesystem', 'format': 'ext4',
                            'mountpoint': '/', 'mountOptions': ['noatime']}
                if 'label' in arguments:
                    expected['extraArgs'] = ['-L', arguments['label']]
                self.assertEqual(json.loads(result.stdout), expected)

    def test_btrfs_keeps_snapshot_store_outside_root_capture(self):
        result = evaluate({'filesystem': 'btrfs', 'label': 'demo-root'})
        self.assertEqual(result.returncode, 0, result.stderr)
        layout = json.loads(result.stdout)
        self.assertEqual(layout['type'], 'btrfs')
        self.assertEqual(layout['extraArgs'], ['-L', 'demo-root'])
        self.assertEqual(layout['subvolumes'], {
            '@root': {'mountpoint': '/', 'mountOptions': ['noatime']},
            '@snapshots': {'mountpoint': '/.snapshots', 'mountOptions': ['noatime']},
            '@swap': {'mountpoint': '/swap', 'mountOptions': ['noatime']}})
        self.assertNotIn('mountpoint', layout)

    def test_unknown_filesystem_is_rejected(self):
        for value in ('xfs', '', None, True):
            with self.subTest(value=value):
                result = evaluate({'filesystem': value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Root filesystem must be ext4 or btrfs.', result.stderr)

    def test_backend_manifests_preserve_declared_root_filesystem_and_network(self):
        cases = [{"backend": backend, "filesystem": filesystem, "spec": {
            "name": "vm-demo-test", "uuid": "12345678-1234-4321-8123-123456789abc",
            "ownerId": "nixberry-12345678-1234-4321-8123-123456789abc", "mac": "52:54:00:12:34:01",
            "memoryMiB": 4096, "vcpus": 2, "diskGiB": 32, "storageDirectory": "/var/lib/vm-demo",
            "volume": "vm-demo-test.qcow2", "machine": "q35", "uri": "qemu:///system",
            "storagePool": "default", "network": "default", "generation": 2, "secureBoot": False, "vmid": 1000,
        }} for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware') for filesystem in ('ext4', 'btrfs')]
        expression = '''let f = builtins.getFlake ''' + nix('path:' + str(ROOT)) + ''';
          pkgs = f.inputs.nixos-raspberrypi.inputs.nixpkgs.legacyPackages.x86_64-linux;
          describe = case: let p = import (''' + str(ROOT) + ''' + "/management/${case.backend}/package.nix") {
            inherit pkgs; inherit (case) spec; rootFilesystem = case.filesystem;
            network = { mode = "static"; address = "192.168.77.50/24"; gateway = "192.168.77.1"; dns = [ "192.168.77.1" ]; };
            installerIso = "/nix/store/fixture-iso"; isoName = "installer.iso";
            targetSystem = "/nix/store/fixture-system"; diskoScript = "/nix/store/fixture-disko";
            guestHostName = "vm-demo-test"; image = "/nix/store/fixture-image";
          }; in builtins.fromJSON p.manifest.text;
        in map describe ''' + nix(cases)
        result = subprocess.run(['nix', 'eval', '--impure', '--json', '--expr', expression],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        for case, manifest in zip(cases, json.loads(result.stdout), strict=True):
            with self.subTest(backend=case['backend'], filesystem=case['filesystem']):
                self.assertEqual(manifest['rootFilesystem'], case['filesystem'])
                if case['backend'] in ('proxmox', 'virtualbox', 'vmware'):
                    self.assertEqual(manifest['target']['rootFilesystem'], case['filesystem'])
                    self.assertEqual(manifest['target']['network']['mode'], 'static')
                else:
                    self.assertEqual(manifest['guestNetwork']['mode'], 'static')
