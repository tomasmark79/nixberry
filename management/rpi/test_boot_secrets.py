"""Exercise the locked upstream bootloader with fixture credentials only."""

from pathlib import Path
import json
import os
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

API = runpy.run_path(str(Path(__file__).with_name("boot-secrets.py")))


class BootSecretsTests(unittest.TestCase):
    def test_runtime_append_is_atomic_with_respect_to_default_generation(self):
        project = Path(__file__).resolve().parents[2]
        upstream = Path(subprocess.check_output(['nix', 'eval', '--impure', '--raw', '--no-write-lock-file',
            '--expr', '(builtins.getFlake ' + json.dumps('path:' + str(project)) + ').inputs.nixos-raspberrypi.outPath'], text=True))
        templates = upstream / 'modules/system/boot/loader/raspberrypi/generational'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_child = '/nix/store/' + 'a' * 32 + '-kernelboot-gen-builder.sh'
            replacements = {'@bash@': str(Path(shutil.which('bash')).parent.parent), '@path@': os.environ['PATH'],
                            '@installDeviceTree@': shutil.which('true'), '@installFirmwareBuilder@': shutil.which('true'),
                            '@nixosGenBuilder@': fake_child, '@nixosGenerationsDir@': 'generations'}
            def replace(text):
                for old, new in replacements.items():
                    text = text.replace(old, new)
                return text
            parent = root / 'loader'
            parent.write_text(replace((templates / 'nixos-generations-builder.sh').read_text()))
            child = replace((templates / 'kernelboot-gen-builder.sh').read_text())
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                return child if str(path) == fake_child else original_read(path, *args, **kwargs)
            output = root / 'patched'
            with patch.object(Path, 'read_text', read):
                API['patch_loader'](parent, output)
            system, boot = root / 'system', root / 'boot'
            system.mkdir()
            boot.mkdir()
            (system / 'kernel').write_bytes(b'fixture kernel')
            (system / 'initrd').write_bytes(b'public initrd')
            (system / 'kernel-params').write_text('root=fstab')
            appender = system / 'append-initrd-secrets'
            command = [str(output / 'loader'), '-c', str(system), '-f', str(boot), '-g', '0']
            for enabled in (False, True):
                if enabled:
                    appender.write_text('#!' + shutil.which('bash') + '\nprintf "%s" " runtime fixture" >> "$1"\n')
                    appender.chmod(0o700)
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = b'public initrd' + (b' runtime fixture' if enabled else b'')
                self.assertEqual((boot / 'generations/default/initrd').read_bytes(), expected)
                self.assertEqual((system / 'initrd').read_bytes(), b'public initrd')
            appender.write_text('#!' + shutil.which('bash') + '\nexit 1\n')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((boot / 'generations/default/initrd').read_bytes(), b'public initrd runtime fixture')

    def test_unknown_upstream_layout_is_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / 'source', Path(temporary) / 'output'
            source.write_text('unexpected upstream loader')
            with self.assertRaisesRegex(ValueError, 'unexpected generation builder'):
                API['patch_loader'](source, output)
            self.assertFalse(output.exists())
