#!/usr/bin/env python3
"""Vytvoří překladovou šablonu z Pythonu, Bashe i PowerShellu bez dalších závislostí."""

import ast
import json
from pathlib import Path
import re
import sys

from messages import _, ArgumentParser, script_messages, error


def collect(root):
    records = {}
    def add(message, path, line):
        if message:
            records.setdefault(message, []).append(f"{path.relative_to(root)}:{line}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.name.startswith("test_") or path.suffix not in (".py", ".sh", ".ps1"):
            continue
        source = path.read_text()
        if path.suffix == ".py":
            tree = ast.parse(source)
            constants = {node.targets[0].id: node.value.value for node in tree.body
                         if isinstance(node, ast.Assign) and len(node.targets) == 1
                         and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)
                         and isinstance(node.value.value, str)}
            constants["__doc__"] = ast.get_docstring(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
                    arg = node.args[0]
                    message = arg.value if isinstance(arg, ast.Constant) else constants.get(arg.id) if isinstance(arg, ast.Name) else None
                    if not isinstance(message, str):
                        if path.name == "messages.py":
                            continue
                        raise ValueError(f"{path}:{node.lineno}: _() potřebuje konstantní celou větu.")
                    add(message, path, node.lineno)
        else:
            for start, end, message in script_messages(source, powershell=path.suffix == ".ps1"):
                add(message, path, source.count("\n", 0, start) + 1)
    return records


def template(root):
    lines = [
        '# Překladová šablona nixberry. Zdrojový jazyk: čeština.',
        '# Generuje management/deployment/extract_messages.py.',
        'msgid ""', 'msgstr ""',
        '"Project-Id-Version: nixberry\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"', '',
    ]
    for message, locations in sorted(collect(root).items()):
        lines.append("#: " + " ".join(sorted(set(locations))))
        if re.search(r"\{[A-Za-z_][^{}]*\}", message):
            lines.append("#, python-brace-format")
        elif re.search(r"%[sd]", message):
            lines.append("#, c-format")
        lines.extend(["msgid " + json.dumps(message, ensure_ascii=False), 'msgstr ""', ''])
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        ArgumentParser(description=_(__doc__)).parse_args()
        sys.stdout.write(template(Path(__file__).resolve().parents[1]))
    except (OSError, ValueError, SyntaxError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
