"""Výběr a přenos média Hyper-V, bez Windows a bez živého SSH."""

import base64
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class HypervMediaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parents[1] / 'hyperv'
        with patch.dict('sys.modules', {'policy': SimpleNamespace(validate=lambda spec: None)}):
            self.manager_class = runpy.run_path(str(self.path / 'manage.py'))['Manager']

    def test_selected_medium_is_hashed_and_uploaded_for_both_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for method in ('image', 'iso'):
                with self.subTest(method=method):
                    path = root / ('system.vhdx' if method == 'image' else 'installer.iso')
                    content = ('fixture ' + method).encode()
                    path.write_bytes(content)
                    manifest = {'deploymentMethod': method, 'imagePath': str(path) if method == 'image' else None,
                                'isoPath': str(path) if method == 'iso' else None,
                                'sshUser': 'fixture', 'sshHost': '10.20.30.40'}
                    with patch.dict('os.environ', {'NIXBERRY_HYPERV_MODULE': str(self.path / 'probe.py')}):
                        manager = self.manager_class(manifest, root)
                    self.assertEqual(manager.sha256, hashlib.sha256(content).hexdigest())
                    self.assertEqual(manager.media_bytes, len(content))
                    with patch.object(manager, 'remote', side_effect=[{}, {'cached': False, 'uploadPath': 'C:\\upload'}, {}]), \
                            patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stderr='')) as run:
                        manager.create(True)
                    self.assertEqual(run.call_args.args[0][-2], str(path))

    def test_default_image_does_not_use_iso_as_a_fallback(self):
        with patch.dict('os.environ', {'NIXBERRY_HYPERV_MODULE': str(self.path / 'probe.py')}):
            with self.assertRaisesRegex(RuntimeError, 'Chybí cesta'):
                self.manager_class({'isoPath': '/unavailable.iso', 'sshUser': 'fixture', 'sshHost': '10.20.30.40'}, '.')

    def test_rpc_uses_neutral_media_size(self):
        manager = self.manager_class.__new__(self.manager_class)
        manager.spec, manager.sha256, manager.media_bytes = {}, 'hash', 42
        manager.host, manager.bootstrap, manager.frame_input = 'fixture', 'fixture', lambda source: source
        with patch.dict('os.environ', {'NIXBERRY_HYPERV_REMOTE': str(self.path / 'host.ps1')}), \
                patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{}', stderr='')) as run:
            manager.remote('plan')
        payload = run.call_args.kwargs['input'].split("FromBase64String('", 1)[1].split("'", 1)[0]
        request = json.loads(base64.b64decode(payload))
        self.assertEqual(request['mediaBytes'], 42)
        self.assertNotIn('isoBytes', request)


if __name__ == '__main__':
    unittest.main()
