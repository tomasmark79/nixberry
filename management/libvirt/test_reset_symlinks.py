"""Exercise reset confirmation and removal using temporary files and fake virsh."""

import contextlib
import io
import json
import os
from pathlib import Path
import pty
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


RESET = runpy.run_path(str(Path(__file__).with_name("reset.py")))


class Terminal(io.StringIO):
    def __init__(self, answer, callback=None):
        super().__init__(answer)
        self.callback = callback

    def isatty(self):
        return True

    def readline(self, *args):
        if self.callback:
            self.callback()
        return super().readline(*args)


class SymlinkResetChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.storage = self.root / "storage"
        self.storage.mkdir()
        self.alias = self.root / "images"
        self.alias.symlink_to(self.storage, target_is_directory=True)
        self.disk = self.alias / "vm-demo-reset.qcow2"
        self.nvram = self.alias / "vm-demo-reset_VARS.fd"
        self.disk.write_bytes(b"owned disk")
        self.nvram.write_bytes(b"owned nvram")
        (self.storage / "unrelated").write_bytes(b"preserve")
        self.manifest = {
            "name": "nixberry-repro-test", "uuid": "315b9a43-1653-4b2f-9cd9-790081000001",
            "ownerId": "nixberry-315b9a43-1653-4b2f-9cd9-790081000001",
            "allowReset": True, "volume": "nixberry-repro-test.qcow2", "diskGiB": 32,
            "uri": "qemu:///system", "storagePool": "default",
            "diskPath": str(self.disk),
        }
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.state = "running"
        self.writes = []
        self.on_shutdown = None
        self.on_undefine = None
        self.peer = False
        self.image_error = False
        self.foreign_volume = False
        self.output, self.diagnostics = io.StringIO(), io.StringIO()

    def execute(self, command, **kwargs):
        if command[0] == "qemu-img":
            if self.image_error:
                raise subprocess.CalledProcessError(1, command)
            return json.dumps({"format": "qcow2", "virtual-size": 32 * 1024**3})
        self.assertEqual(command[:3], ["virsh", "--connect", "qemu:///system"])
        action = command[3]
        if action == "vol-dumpxml":
            path = "/foreign-volume.qcow2" if self.foreign_volume else self.manifest["diskPath"]
            return ("<volume><name>" + self.manifest["volume"]
                    + "</name><capacity>34359738368</capacity><target><path>" + path
                    + "</path><format type='qcow2'/></target></volume>")
        if action == "dumpxml":
            if command[4] == "peer":
                return '<domain><devices><disk><source file="' + str(self.disk.resolve()) + '"/></disk></devices></domain>'
            return "<fixture/>"
        if action == "domstate":
            return self.state
        if action == "snapshot-list":
            return ""
        if action == "list":
            return "peer" if self.peer else ""
        self.assertIn(action, ("shutdown", "undefine", "vol-delete"))
        self.writes.append(action)
        if action == "shutdown":
            self.state = "shut off"
            if self.on_shutdown:
                self.on_shutdown()
        elif action == "undefine":
            self.assertEqual(self.state, "shut off")
            self.nvram.unlink()
            if self.on_undefine:
                self.on_undefine()
        else:
            self.disk.unlink()
        return ""

    def run_reset(self, arguments, stream):
        globals_ = RESET["main"].__globals__
        with patch.dict(os.environ, {"NIXBERRY_MANIFEST": str(self.manifest_path)}), \
                patch.object(sys, "argv", ["reset", *arguments]), patch.object(sys, "stdin", stream), \
                patch.object(globals_["policy"], "validate"), \
                patch.dict(globals_, {"validate_target": lambda *args: (str(self.disk), str(self.nvram))}), \
                patch.object(subprocess, "check_output", side_effect=self.execute), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.diagnostics):
            RESET["main"]()

    def redirect(self):
        other = self.root / "other"
        other.mkdir()
        (other / self.disk.name).write_bytes(b"foreign disk")
        (other / self.nvram.name).write_bytes(b"foreign nvram")
        self.alias.unlink()
        self.alias.symlink_to(other, target_is_directory=True)

    def test_preview_lists_real_paths_without_prompt_or_writes(self):
        stream = io.StringIO("y\n")
        self.run_reset([], stream)
        self.assertEqual(stream.tell(), 0)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state, "running")
        self.assertIn(str(self.disk.resolve()), self.diagnostics.getvalue())
        self.assertIn("Directory symlink:", self.diagnostics.getvalue())
        self.assertEqual(self.output.getvalue(), "")

    def test_yes_removes_only_owned_files_and_preserves_link_and_directory(self):
        self.run_reset(["--apply"], Terminal("y\n"))
        self.assertEqual(self.writes, ["shutdown", "undefine", "vol-delete"])
        self.assertFalse(self.disk.exists())
        self.assertFalse(self.nvram.exists())
        self.assertTrue(self.alias.is_symlink())
        self.assertTrue(self.storage.is_dir())
        self.assertEqual((self.storage / "unrelated").read_bytes(), b"preserve")
        self.assertIn("[y/N]", self.diagnostics.getvalue())
        self.assertEqual(self.output.getvalue(), "")

    def test_no_empty_invalid_and_eof_cancel_before_shutdown(self):
        for answer in ("n\n", "\n", "", "invalid\n"):
            with self.subTest(answer=answer):
                self.run_reset(["--apply"], Terminal(answer))
                self.assertEqual(self.writes, [])
                self.assertTrue(self.disk.exists())
                self.assertIn("Removal cancelled", self.diagnostics.getvalue())

    def test_piped_yes_cannot_replace_interactive_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
            self.run_reset(["--apply"], io.StringIO("y\n"))
        self.assertEqual(self.writes, [])

    def test_plain_paths_keep_existing_noninteractive_apply(self):
        self.disk, self.nvram = self.disk.resolve(), self.nvram.resolve()
        self.run_reset(["--apply"], io.StringIO())
        self.assertEqual(self.writes, ["shutdown", "undefine", "vol-delete"])
        self.assertNotIn("[y/N]", self.diagnostics.getvalue())

    def test_redirect_during_confirmation_stops_before_shutdown(self):
        with self.assertRaisesRegex(RuntimeError, "Reset paths changed"):
            self.run_reset(["--apply"], Terminal("y\n", self.redirect))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.disk.read_bytes(), b"foreign disk")
        self.assertEqual((self.storage / self.disk.name).read_bytes(), b"owned disk")

    def test_redirect_during_shutdown_stops_before_removal(self):
        self.on_shutdown = self.redirect
        with self.assertRaisesRegex(RuntimeError, "Reset paths changed"):
            self.run_reset(["--apply"], Terminal("y\n"))
        self.assertEqual(self.writes, ["shutdown"])
        self.assertTrue((self.storage / self.nvram.name).exists())
        self.assertEqual(self.disk.read_bytes(), b"foreign disk")

    def test_replaced_disk_during_shutdown_stops_before_removal(self):
        def replace():
            self.disk.rename(self.storage / "saved-original")
            self.disk.write_bytes(b"replacement")
        self.on_shutdown = replace
        with self.assertRaisesRegex(RuntimeError, "Reset paths changed"):
            self.run_reset(["--apply"], Terminal("y\n"))
        self.assertEqual(self.writes, ["shutdown"])
        self.assertTrue(self.nvram.exists())

    def test_redirect_after_undefine_preserves_both_disks(self):
        self.on_undefine = self.redirect
        with self.assertRaisesRegex(RuntimeError, "Reset paths changed"):
            self.run_reset(["--apply"], Terminal("y\n"))
        self.assertEqual(self.writes, ["shutdown", "undefine"])
        self.assertEqual(self.disk.read_bytes(), b"foreign disk")
        self.assertTrue((self.storage / self.disk.name).exists())

    def test_peer_reference_through_real_path_still_refuses_reset(self):
        self.peer = True
        with self.assertRaisesRegex(RuntimeError, "shared with another VM"):
            self.run_reset(["--apply"], Terminal("y\n"))
        self.assertEqual(self.writes, [])

    def test_changed_volume_after_undefine_prevents_volume_deletion(self):
        self.on_undefine = lambda: setattr(self, "foreign_volume", True)
        with self.assertRaisesRegex(RuntimeError, "Volume path does not match"):
            self.run_reset(["--apply"], Terminal("y\n"))
        self.assertEqual(self.writes, ["shutdown", "undefine"])
        self.assertTrue(self.disk.exists())

    def test_unreadable_image_reports_resolved_path_without_prompt_or_writes(self):
        self.image_error = True
        with self.assertRaisesRegex(RuntimeError, "Check read access") as failure:
            self.run_reset(["--apply"], Terminal("y\n"))
        self.assertIn(str(self.disk.resolve()), str(failure.exception))
        self.assertIn("Resolved reset path:", self.diagnostics.getvalue())
        self.assertNotIn("[y/N]", self.diagnostics.getvalue())
        self.assertEqual(self.writes, [])

    def test_interrupt_at_confirmation_does_not_shutdown(self):
        def interrupt():
            raise KeyboardInterrupt
        self.run_reset(["--apply"], Terminal("y\n", interrupt))
        self.assertEqual(self.writes, [])
        self.assertIn("Removal cancelled", self.diagnostics.getvalue())


@unittest.skipUnless(shutil.which("msgfmt"), "GNU gettext required")
class NativeConfirmationChecks(unittest.TestCase):
    def test_real_terminal_confirmation_languages_and_streams(self):
        deployment = Path(__file__).resolve().parents[1] / "deployment"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(deployment / "messages.py", root / "messages.py")
            catalog = root / "locales/cs/LC_MESSAGES/nixberry.mo"
            catalog.parent.mkdir(parents=True)
            subprocess.run(["msgfmt", "-o", str(catalog),
                            str(deployment / "locales/cs/LC_MESSAGES/nixberry.po")], check=True)
            for language in ("en", "cs", "unknown"):
                for answer, expected in ((b"n\n", 3), (b"Y\n", 0)):
                    with self.subTest(language=language, answer=answer):
                        master, slave = pty.openpty()
                        self.addCleanup(os.close, master)
                        environment = {**os.environ, "PYTHONPATH": os.pathsep.join(
                            [str(root), str(Path(__file__).parent), str(deployment)]),
                            "NIXBERRY_LANG": language, "LC_ALL": "C", "NO_COLOR": "1", "TERM": "dumb"}
                        code = "import runpy,sys; r=runpy.run_path(sys.argv[1]); sys.exit(0 if r['confirm_paths']() else 3)"
                        process = subprocess.Popen([sys.executable, "-c", code,
                            str(Path(__file__).with_name("reset.py"))], stdin=slave,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
                        os.close(slave)
                        try:
                            os.write(master, answer)
                            stdout, stderr = process.communicate(timeout=10)
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.communicate()
                        self.assertEqual(process.returncode, expected, stderr)
                        self.assertEqual(stdout, "")
                        self.assertIn("[y/N]", stderr)
                        self.assertIn("Odstranit tuto VM" if language == "cs" else "Remove this VM", stderr)
                        self.assertNotIn("\x1b", stderr)


if __name__ == "__main__":
    unittest.main()
