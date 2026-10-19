"""Katalog a výstupní kontrakt chrání nové příkazy před opomenutými hláškami."""

import ast
from pathlib import Path
import re
import subprocess
import sys
import unittest

from extract_messages import collect, template


ROOT = Path(__file__).resolve().parents[1]


class MessageContractChecks(unittest.TestCase):
    def test_english_sources_and_complete_czech_catalogues(self):
        czech_letters = re.compile('[ěščřžýáíéůúďťňĚŠČŘŽÝÁÍÉŮÚĎŤŇ]')
        self.assertEqual([message for message in collect(ROOT) if czech_letters.search(message)], [])
        result = subprocess.run([sys.executable, str(ROOT / 'deployment/compile_locales.py'), '--check'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_translation_template_covers_current_python_shell_and_powershell_sources(self):
        self.assertEqual((ROOT / "deployment/locales/nixberry.pot").read_text(), template(ROOT),
                         "Obnov katalog přes management/deployment/extract_messages.py")
        locations = {location.split(":")[0] for values in collect(ROOT).values() for location in values}
        for source in ("deployment/cli.py", "deployment/exporter.py", "deployment/cleanup.py",
                       "deployment/host_agent.py", "libvirt/install.py", "libvirt/create.sh",
                       "hyperv/manage.py", "hyperv/host.ps1", "hyperv/guest-install.sh",
                       "rpi/install.py", "rpi/local-install.py", "rpi/guest-install.sh"):
            self.assertIn(source, locations)

    def test_python_user_messages_do_not_print_unmarked_literal_text(self):
        errors = []
        for path in ROOT.rglob("*.py"):
            if path.is_symlink() or path.name.startswith("test_") or path.name == "messages.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("info", "success", "warning") and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute) and first.func.attr == "format":
                        errors.append(f"{path}:{node.lineno}: hodnoty předávej do helperu, neformátuj je dvakrát")
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "print":
                    continue
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and re.search(r"[A-Za-zá-ž]", argument.value):
                        errors.append(f"{path}:{node.lineno}: {argument.value}")
                    if isinstance(argument, ast.JoinedStr):
                        errors.append(f"{path}:{node.lineno}: přímý f-string místo přeloženého textu")
        self.assertEqual(errors, [])

    def test_shell_message_templates_are_literal_and_no_interpolated_fail_calls_remain(self):
        errors = []
        for path in ROOT.rglob("*.sh"):
            if path.name in ("messages.sh", "probe.sh"):
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "declare -F nb_info" in line:
                    continue
                for call in re.finditer(r'\b(?:nb_info|nb_success|nb_warning|nb_error|nb_help|fail)\s+([^\s])', line):
                    if call[1] != "'" and 'nb_error "$@"' not in line:
                        errors.append(f"{path}:{number}: {line}")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
