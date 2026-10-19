"""Společný kontrakt balíčků všech VM; pouze izolované buildy a odmítnuté operace."""

import json
from pathlib import Path
import subprocess
import unittest

from check_inventory import nix
from cli import operations


BACKENDS = ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware")


class PlatformContractTests(unittest.TestCase):
    def test_cli_and_nix_agree_on_capabilities_for_every_backend(self):
        recipe = Path(__file__).resolve().parents[2]
        targets = []
        for backend in (*BACKENDS, 'rpi'):
            for offline, access in ((False, False), (True, False), (True, True)):
                if backend == 'rpi' and offline:
                    continue
                target = {'backend': backend, 'offline': offline,
                          'commands': {key: key for key in ('probe', 'create', 'install', 'provision', 'reset')} if not offline and backend != 'rpi' else {}}
                if access:
                    target['access'] = {'host': '10.0.1.19', 'user': 'root', 'port': 22}
                target['capabilities'] = {'system': not offline or access, 'hypervisor': not offline and backend != 'rpi'}
                targets.append(target)
        expression = 'map (import ' + str(recipe / 'management/deployment/operations.nix') + ') ' + nix(targets)
        result = subprocess.run(['nix', 'eval', '--impure', '--json', '--expr', expression],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        for target, actual in zip(targets, json.loads(result.stdout), strict=True):
            with self.subTest(backend=target['backend'], offline=target['offline']):
                self.assertEqual(set(actual), set(operations(target)))

    def expression(self, backend, method, *, media=True):
        recipe = Path(__file__).resolve().parents[2]
        spec = {
            "name": "nixberry-fixture", "uuid": "f04a8a56-f2e2-4062-8f97-b9e8795e30ea",
            "ownerId": "nixberry-f04a8a56-f2e2-4062-8f97-b9e8795e30ea", "mac": "52:54:00:12:34:56",
            "memoryMiB": 4096, "vcpus": 4, "diskGiB": 32,
            "uri": "qemu:///system", "machine": "q35", "network": "default",
            "storagePool": "default", "storageDirectory": "/unreachable/nixberry-fixture",
            "volume": "nixberry-fixture.qcow2", "transport": "ssh", "hostPlatform": "linux",
            "hostName": "fixture.invalid", "sshHost": "fixture.invalid", "sshUser": "root",
        }
        value = 'fixture' if media else '(throw "Reset nesmí vyhodnocovat systém ani médium")'
        return (
            'let f = builtins.getFlake ' + nix("path:" + str(recipe)) + '; '
            'pkgs = f.inputs.nixos-raspberrypi.inputs.nixpkgs.legacyPackages.x86_64-linux; '
            'fixture = pkgs.writeText "nixberry-contract-fixture" "fixture"; '
            'tools = import ' + str(recipe / "management" / backend / "package.nix") + ' { inherit pkgs; '
            'spec = ' + nix(spec) + '; guestHostName = "fixture"; allowReset = false; '
            'deploymentMethod = ' + nix(method) + '; isoName = "fixture.iso"; '
            + ' '.join(key + ' = ' + value + ';' for key in ('image', 'installerIso', 'targetSystem', 'diskoScript'))
            + ' }; in '
        )

    def build(self, expression):
        result = subprocess.run(['nix', 'build', '--impure', '--no-link', '--print-out-paths', '--expr', expression],
                                text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        return Path(result.stdout.strip())

    def test_every_backend_has_the_same_actions_for_both_methods(self):
        for backend in BACKENDS:
            for method in ('image', 'iso'):
                with self.subTest(backend=backend, method=method):
                    root = self.build(self.expression(backend, method) + 'tools')
                    prefix = 'vm' if backend == 'libvirt' else backend
                    expected = {f'nixberry-{prefix}-{action}' for action in ('create', 'install', 'provision', 'reset')}
                    expected.add(f'nixberry-{backend}-probe')
                    self.assertEqual({p.name for p in (root / 'bin').iterdir()}, expected)

    def test_reset_needs_no_media_and_refuses_apply_without_permission(self):
        for backend in BACKENDS:
            paths = []
            for method in ('image', 'iso'):
                with self.subTest(backend=backend, method=method):
                    root = self.build(self.expression(backend, method, media=False) + 'tools.reset')
                    paths.append(root)
                    binary = next((root / 'bin').iterdir())
                    result = subprocess.run([str(binary), '--apply'], capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertRegex(result.stderr, 'allowReset|není deklarativně povolené')
                    self.assertEqual(result.stdout, '')
            self.assertEqual(paths[0], paths[1], backend)

    def test_manifests_distinguish_image_and_iso_for_every_backend(self):
        for backend in BACKENDS:
            for method in ('image', 'iso'):
                with self.subTest(backend=backend, method=method):
                    path = self.build(self.expression(backend, method) + 'tools.manifest')
                    manifest = json.loads(path.read_text())
                    self.assertEqual(manifest['deploymentMethod'], method)
                    self.assertIsNone(manifest['imagePath' if method == 'iso' else 'isoPath'])
                    self.assertTrue(manifest['isoPath' if method == 'iso' else 'imagePath'])


if __name__ == '__main__':
    unittest.main()
