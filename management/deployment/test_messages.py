"""Překlady, proudy a barvy mají stejné chování nezávisle na prostředí volajícího."""

import contextlib
import gettext
import io
import json
import os
import shutil
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import messages

BASH = shutil.which("bash")


class Stream(io.StringIO):
    def __init__(self, terminal=False):
        super().__init__()
        self.terminal = terminal

    def isatty(self):
        return self.terminal


class MessageChecks(unittest.TestCase):
    def test_image_checker_entrypoint_keeps_error_off_data_stream(self):
        path = Path(__file__).with_name("check_images.py")
        result = subprocess.run([sys.executable, str(path), "/nonexistent-nixberry-message-test.qcow2"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("[nixberry]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_dynamic_values_with_braces_are_never_formatted_again(self):
        with contextlib.redirect_stderr(Stream()) as stderr:
            messages.info("VM: {message}; stav {level}; šablona {template}; proud {stream}",
                          message='{"vm": "lab"}', level="{running}", template="test", stream="stdout")
        self.assertEqual(stderr.getvalue(), '[nixberry] VM: {"vm": "lab"}; stav {running}; šablona test; proud stdout\n')

    def test_parser_errors_are_czech_on_stderr_and_keep_exit_code_two(self):
        cases = (
            (["--unknown"], "Neznámé argumenty"),
            (["--host"], "potřebuje hodnotu"),
            (["--apply=yes"], "nepřijímá hodnotu"),
            (["--apply", "--dry-run"], "nelze kombinovat"),
            (["invalid"], "nepovolená hodnota"),
        )
        for argv, expected in cases:
            parser = messages.ArgumentParser(prog="test")
            parser.add_argument("--host")
            parser.add_argument("action", nargs="?", choices=("list", "show"))
            modes = parser.add_mutually_exclusive_group()
            modes.add_argument("--apply", action="store_true")
            modes.add_argument("--dry-run", action="store_true")
            stdout, stderr = Stream(), Stream()
            with self.subTest(argv=argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as failure:
                    parser.parse_args(argv)
            self.assertEqual(failure.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn(expected, stderr.getvalue())
            self.assertIn("Použití:", stderr.getvalue())
            self.assertEqual(stderr.getvalue().count("[nixberry]"), 1)

    def test_nested_own_error_has_one_prefix_and_preserves_braces(self):
        with contextlib.redirect_stderr(Stream()) as stderr:
            messages.error("{error}", error="[nixberry] Původní {detail}")
        self.assertEqual(stderr.getvalue(), "[nixberry] Původní {detail}\n")

    @unittest.skipUnless(BASH, "Shellová kontrola potřebuje Bash")
    def test_shell_helper_translates_once_and_preserves_data_without_path_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "script.sh"
            path.write_text("nb_info 'Cíl: %s' \"$1\"\nprintf '%s\\n' '{\"ok\":true}'\n")
            with patch.object(messages, "_", side_effect=lambda text: {"Cíl: %s": "Target: %s"}.get(text, text)), \
                    patch.object(sys, "stderr", Stream()):
                script = messages.shell_script(path)
            result = subprocess.run([BASH, "-s", "--", "lab; $(exit 99)"],
                                    input=script, text=True, capture_output=True, env={"PATH": ""})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"ok": True})
        self.assertEqual(result.stderr, "[nixberry] Target: lab; $(exit 99)\n")

    @unittest.skipUnless(BASH, "Shellová kontrola potřebuje Bash")
    def test_remote_shell_color_policy_and_no_color(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "script.sh"
            path.write_text("nb_warning 'Pozor.'\n")
            for terminal, environment, colored in (
                (True, {"TERM": "xterm"}, True), (False, {"TERM": "xterm"}, False),
                (True, {"NO_COLOR": ""}, False), (True, {"TERM": "dumb"}, False),
            ):
                with patch.dict(os.environ, environment, clear=True), patch.object(sys, "stderr", Stream(terminal)):
                    script = messages.shell_script(path)
                result = subprocess.run([BASH, "-s"], input=script,
                                        env={"PATH": ""}, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0)
                self.assertEqual("\033[" in result.stderr, colored)
                self.assertEqual(result.stdout, "")

    def test_powershell_translation_does_not_change_commands_and_quotes_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.ps1"
            path.write_text("Require ($value -eq 'technical') 'Chyba.'\nthrow 'Jiná chyba.'\n")
            with patch.object(messages, "_", side_effect=lambda text: "Don't change $value or $(exit 99)"):
                script = messages.localized_script(path)
        self.assertIn("($value -eq 'technical')", script)
        self.assertEqual(script.count("'Don''t change $value or $(exit 99)'"), 2)

    def test_levels_use_only_stderr_and_flush(self):
        stdout, stderr = Stream(), Stream()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch.object(stderr, "flush", wraps=stderr.flush) as flush:
            for emit in (messages.info, messages.success, messages.warning, messages.error):
                emit("Cíl: {name}", name="lab")
            self.assertEqual(flush.call_count, 4)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "[nixberry] Cíl: lab\n" * 4)

    def test_colors_follow_destination_stream_and_environment(self):
        for terminal, environment, colored in (
            (True, {"TERM": "xterm"}, True), (False, {"TERM": "xterm"}, False),
            (True, {"NO_COLOR": ""}, False), (True, {"TERM": "dumb"}, False),
        ):
            for level, color in (("info", "36"), ("success", "32"), ("warning", "33"), ("error", "31")):
                with self.subTest(terminal=terminal, environment=environment, level=level), \
                        patch.dict(os.environ, environment, clear=True):
                    result = messages.render(level, "Cíl: {name}", stream=Stream(terminal), name="lab")
                    self.assertEqual("\033[" in result, colored)
                    if colored:
                        self.assertEqual(result, f"\033[{color}m[nixberry]\033[0m Cíl: lab")

    def test_czech_default_and_missing_catalog_fallback(self):
        for environment in ({"LANG": "en_US.UTF-8"}, {"NIXBERRY_LANG": "en"}, {"NIXBERRY_LANG": "xx"}):
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(messages._("Přenáším systém…"), "Přenáším systém…")

    def test_translation_precedes_named_substitution(self):
        class English(gettext.NullTranslations):
            def gettext(self, message):
                return "Target {target}: copying to {host}."

        with patch.dict(os.environ, {"NIXBERRY_LANG": "en"}), \
                patch.object(messages, "_catalog", return_value=English()) as catalog:
            text = messages.render("info", messages._("Přenáším na {host} cíl {target}."),
                                   target="lab", host="root@server", stream=Stream())
        catalog.assert_called_once_with("en")
        self.assertEqual(text, "[nixberry] Target lab: copying to root@server.")

    def test_real_gettext_catalog_and_untranslated_fallback(self):
        # Malý binární GNU MO katalog: ověřujeme skutečné načtení, nikoli jen mock překladače.
        entries = sorted({
            "": "Content-Type: text/plain; charset=UTF-8\nLanguage: en\n",
            "Cíl: {name}": "Target: {name}",
        }.items())
        originals = b"".join(key.encode() + b"\0" for key, _ in entries)
        translations = b"".join(value.encode() + b"\0" for _, value in entries)
        offset = 28 + 16 * len(entries)
        tables = []
        for values in ([key for key, _ in entries], [value for _, value in entries]):
            table = b""
            for value in values:
                encoded = value.encode()
                table += struct.pack("<2I", len(encoded), offset)
                offset += len(encoded) + 1
            tables.append(table)
        catalog = struct.pack("<7I", 0x950412de, 0, len(entries), 28, 28 + 8 * len(entries), 0, 0)
        module_source = Path(messages.__file__).resolve().read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "locales/en/LC_MESSAGES/nixberry.mo"
            target.parent.mkdir(parents=True)
            target.write_bytes(catalog + b"".join(tables) + originals + translations)
            (root / "messages.py").write_text(module_source)
            agent = root / "agent.py"
            agent.write_text('from messages import _\nimport json\nprint(json.dumps({"label": _("Cíl: {name}").format(name="lab")}))\n')
            messages._catalog.cache_clear()
            try:
                with patch.object(messages, "__file__", str(root / "messages.py")), \
                        patch.dict(os.environ, {"NIXBERRY_LANG": "en"}):
                    self.assertEqual(messages._("Cíl: {name}").format(name="lab"), "Target: lab")
                    self.assertEqual(messages._("Chybějící překlad"), "Chybějící překlad")
                    remote = messages.python_script(agent)
                result = subprocess.run([sys.executable, "-I", "-c", remote], cwd=directory,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"label": "Target: lab"})
                self.assertEqual(result.stderr, "")
            finally:
                messages._catalog.cache_clear()


if __name__ == "__main__":
    unittest.main()
