"""Společné hlášky: český výchozí text, gettext, stderr a střídmé barvy."""

from functools import lru_cache
import argparse
import gettext
import os
from pathlib import Path
import re
import shlex
import sys


@lru_cache(maxsize=2)
def _catalog(language):
    return gettext.translation(
        "nixberry", localedir=Path(__file__).resolve().with_name("locales"),
        languages=[language, "cs"] if language != "cs" else ["cs"], fallback=True,
    )


def _(message):
    # Jazyk CLI je nezávislý na LC_ALL=C, které používáme k parsování cizích nástrojů.
    language = os.environ.get("NIXBERRY_LANG", "cs")
    return _catalog(language if language in ("cs", "en") else "cs").gettext(message)


def color_enabled(stream):
    return stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def _prefix(kind, stream):
    prefix = "[nixberry]"
    if color_enabled(stream):
        color = {"info": "36", "success": "32", "warning": "33", "error": "31"}[kind]
        prefix = f"\033[{color}m{prefix}\033[0m"
    return prefix


def render(kind, template, /, *, stream=None, **values):
    """Formátuj až přeloženou větu; obarvi pouze označení, nikoli vložené hodnoty."""
    return _prefix(kind, sys.stderr if stream is None else stream) + " " + template.format(**values)


def _emit(kind, template, /, **values):
    # Pole šablony nesmějí kolidovat s interními jmény parametrů ani s volbou proudu.
    print(_prefix(kind, sys.stderr) + " " + template.format(**values), file=sys.stderr, flush=True)


def info(template, /, **values):
    _emit("info", template, **values)


def success(template, /, **values):
    _emit("success", template, **values)


def warning(template, /, **values):
    _emit("warning", template, **values)


def error(template, /, **values):
    # Zachycená chyba vlastního podprocesu dostane označení jen jednou.
    detail = template.format(**values).removeprefix("[nixberry] ")
    _emit("error", "{detail}", detail=detail)


def forward_stderr(value):
    """Předej diagnostiku cizího nástroje beze změn a bez označení nixberry."""
    if value:
        sys.stderr.write(value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value)
        sys.stderr.flush()


def script_messages(source, *, powershell=False):
    """Najdi pouze vyznačené konstantní hlášky, nikdy příkazy nebo datové hodnoty."""
    if powershell:
        offset = 0
        for line in source.splitlines(keepends=True):
            tokens = list(re.finditer(r"'(?:[^']|'')*'", line))
            if line.lstrip().startswith("Require ") and tokens:
                token = tokens[-1]
                yield offset + token.start(), offset + token.end(), token[0][1:-1].replace("''", "'")
            for match in re.finditer(r"\bthrow\s+('(?:[^']|'')*')", line, re.I):
                yield offset + match.start(1), offset + match.end(1), match[1][1:-1].replace("''", "'")
            offset += len(line)
    else:
        for match in re.finditer(r"\b(?:nb_info|nb_success|nb_warning|nb_error|nb_help|fail)\s+('(?:[^']*)')", source):
            yield match.start(1), match.end(1), match[1][1:-1]


def localized_script(path):
    """Přelož literal hlášky před přenosem; vzdálený host nepotřebuje gettext."""
    path = Path(path)
    source = path.read_text()
    powershell = path.suffix == ".ps1"
    for start, end, message in sorted(script_messages(source, powershell=powershell), reverse=True):
        translated = _(message)
        quoted = "'" + translated.replace("'", "''") + "'" if powershell else shlex.quote(translated)
        source = source[:start] + quoted + source[end:]
    return source


def shell_script(path):
    """Předej helper jednou pro celý skript a barvy zvol podle místního stderr."""
    helper = Path(__file__).resolve().with_name("messages.sh").read_text()
    color = "always" if color_enabled(sys.stderr) else "never"
    return f"export NIXBERRY_COLOR={color}\n" + helper + "\n" + localized_script(path)


def python_script(path):
    """Samostatnému RPC procesu přibal modul i případný katalog; stdout zůstává JSON."""
    root = Path(__file__).resolve().parent
    language = os.environ.get("NIXBERRY_LANG", "cs")
    language = language if language in ("cs", "en") else "cs"
    catalog = root / "locales" / language / "LC_MESSAGES/nixberry.mo"
    prelude = (
        "import sys, types, os, gettext, io\n"
        f"os.environ['NIXBERRY_LANG'] = {language!r}\n"
        "_nb_messages = types.ModuleType('messages')\n"
        "_nb_messages.__file__ = '__nixberry_messages__.py'\n"
        "sys.modules['messages'] = _nb_messages\n"
        f"exec({(root / 'messages.py').read_text()!r}, _nb_messages.__dict__)\n"
    )
    if catalog.is_file():
        prelude += (f"_nb_catalog = gettext.GNUTranslations(io.BytesIO({catalog.read_bytes()!r}))\n"
                    "_nb_messages._catalog = lambda language: _nb_catalog\n")
    return prelude + Path(path).read_text()


class _UsageFormatter:
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, _("Použití: ") if prefix is None else prefix)


class ArgumentParser(argparse.ArgumentParser):
    """Stejné chyby a české nadpisy také u samostatných ovládacích příkazů."""
    def __init__(self, *args, **kwargs):
        formatter = kwargs.get("formatter_class", argparse.HelpFormatter)
        kwargs["formatter_class"] = type("MessageFormatter", (_UsageFormatter, formatter), {})
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = _("Argumenty")
        self._optionals.title = _("Volby")
        if add_help:
            self.add_argument("-h", "--help", action="help", help=_("Zobrazit nápovědu a skončit"))

    def error(self, message):
        # Běžné chyby argparse překládáme bez globální změny jeho gettext či locale.
        patterns = (
            (r"unrecognized arguments: (?P<arguments>.*)", _("Neznámé argumenty: {arguments}")),
            (r"the following arguments are required: (?P<arguments>.*)", _("Chybí povinné argumenty: {arguments}")),
            (r"one of the arguments (?P<arguments>.*) is required", _("Zadej jednu z voleb: {arguments}")),
            (r"argument (?P<option>.*?): expected one argument", _("Volba {option} potřebuje hodnotu.")),
            (r"argument (?P<option>.*?): ignored explicit argument (?P<value>.*)",
             _("Volba {option} nepřijímá hodnotu {value}.")),
            (r"argument (?P<option>.*?): not allowed with argument (?P<other>.*)",
             _("Volby {option} a {other} nelze kombinovat.")),
            (r"argument (?P<option>.*?): invalid choice: (?P<value>.*?) \(choose from (?P<choices>.*)\)",
             _("Argument {option}: nepovolená hodnota {value}. Možnosti: {choices}")),
        )
        for pattern, template in patterns:
            match = re.fullmatch(pattern, message)
            if match:
                message = template.format(**match.groupdict())
                break
        self.print_usage(sys.stderr)
        error("{error}", error=message)
        self.exit(2)
