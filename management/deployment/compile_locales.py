#!/usr/bin/env python3
"""Compile Czech gettext and shell catalogues; --check verifies generated shell content."""

import gettext
from pathlib import Path
import shlex
import subprocess
import tempfile

from extract_messages import collect
from messages import _, ArgumentParser, script_messages, error


def shell_catalog(root, catalog):
    messages = set()
    for path in sorted(root.rglob('*.sh')):
        if path.is_symlink() or path.name == 'cs.sh':
            continue
        messages.update(message for _, _, message in script_messages(path.read_text()))
    lines = ['# Generated Czech shell messages. Update with compile_locales.py.',
             '_nb_translate() {', '  REPLY=$1', '  case $1 in']
    for message in sorted(messages):
        translated = catalog.gettext(message)
        lines.append('    ' + shlex.quote(message) + ') REPLY=' + shlex.quote(translated) + ' ;;')
    return '\n'.join([*lines, '  esac', '}', ''])


def main():
    parser = ArgumentParser(description=_(__doc__))
    parser.add_argument('--check', action='store_true', help=_('Check catalogue coverage and generated shell content'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = root / 'deployment/locales'
    with tempfile.TemporaryDirectory() as temporary:
        binary = Path(temporary) / 'nixberry.mo'
        subprocess.run(['msgfmt', '--check', '-o', str(binary),
                        str(directory / 'cs/LC_MESSAGES/nixberry.po')], check=True)
        with binary.open('rb') as stream:
            catalog = gettext.GNUTranslations(stream)
        missing = sorted(set(collect(root)) - set(catalog._catalog))
        if missing:
            parser.error(_('Missing Czech translations: {messages}').format(messages=repr(missing)))
        shell = shell_catalog(root, catalog)
        if args.check:
            if (directory / 'cs.sh').read_text() != shell:
                parser.error(_('Regenerate shell catalogue with compile_locales.py'))
        else:
            (directory / 'cs/LC_MESSAGES/nixberry.mo').write_bytes(binary.read_bytes())
            (directory / 'cs.sh').write_text(shell)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as failure:
        error('{error}', error=failure)
        raise SystemExit(1)
