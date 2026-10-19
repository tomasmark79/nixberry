"""Úspěšné podprocesy zachovají diagnostiku na stderr a čistá data na stdout."""

import contextlib
import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import host_agent
import messages
import portable
import runtime


class DiagnosticTests(unittest.TestCase):
    def test_raw_diagnostics_preserve_content_without_prefix_or_color(self):
        for value in ('Varování nástroje\n', 'Varování nástroje\n'.encode(), b'unknown:\xff\n', '', None):
            with self.subTest(value=value), contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                messages.forward_stderr(value)
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(err.getvalue(), value.decode(errors='replace') if isinstance(value, bytes) else value or '')

    def test_runtime_and_host_agent_keep_success_diagnostics(self):
        for operation, binary in ((runtime.read, False), (host_agent.run, True)):
            result = SimpleNamespace(returncode=0, stdout=b'data\n' if binary else 'data\n',
                                     stderr=b'warning\n' if binary else 'warning\n')
            with self.subTest(operation=operation.__name__), patch('subprocess.run', return_value=result), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(operation(['fixture']), 'data')
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(err.getvalue(), 'warning\n')

    def test_rpc_preserves_host_and_transport_diagnostics_for_each_portable_backend(self):
        for backend in portable.BACKENDS:
            for transport in ('local', 'ssh'):
                target = {'backend': backend, 'spec': {'transport': transport}}
                result = SimpleNamespace(returncode=0, stdout=b'{"ok":true,"result":{"state":"absent"}}',
                                         stderr=b'host warning\nssh warning\n')
                with self.subTest(backend=backend, transport=transport), \
                        patch.object(portable, 'validate'), patch.object(portable, 'transport', return_value=['python']), \
                        patch('subprocess.run', return_value=result), contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(portable.rpc(target, 'probe'), {'state': 'absent'})
                    self.assertEqual(out.getvalue(), '')
                    self.assertEqual(err.getvalue(), 'host warning\nssh warning\n')

    def test_error_is_left_to_caller_without_duplicate_stderr(self):
        result = SimpleNamespace(returncode=1, stdout='', stderr='failure\n')
        with patch('subprocess.run', return_value=result), contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                runtime.read(['fixture'])
            self.assertEqual(err.getvalue(), '')

    def test_hyperv_remote_keeps_json_separate_from_success_diagnostics(self):
        path = Path(__file__).resolve().parents[1] / 'hyperv/manage.py'
        with patch.dict('sys.modules', {'policy': SimpleNamespace(validate=lambda spec: None)}):
            manager_class = runpy.run_path(str(path))['Manager']
        manager = manager_class.__new__(manager_class)
        manager.spec, manager.sha256, manager.media_bytes = {}, 'hash', 1
        manager.host, manager.bootstrap, manager.frame_input = 'fixture', 'fixture', lambda source: source
        result = SimpleNamespace(returncode=0, stdout='{"state":"Off"}', stderr='Windows warning\n')
        with patch.dict('os.environ', {'NIXBERRY_HYPERV_REMOTE': str(path.with_name('host.ps1'))}), \
                patch('subprocess.run', return_value=result), contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(manager.remote('status'), {'state': 'Off'})
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(err.getvalue(), 'Windows warning\n')


if __name__ == '__main__':
    unittest.main()
