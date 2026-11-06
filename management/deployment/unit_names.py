"""One management-unit boundary for capture and destination preparation."""
import json
from pathlib import Path
import re

RULES = json.loads(Path(__file__).with_name('protected-units.json').read_text())
MANAGEMENT = set(RULES['management'])
CAPTURE = MANAGEMENT | set(RULES['captureOnly'])


def valid(value, *, capture=True):
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r'([A-Za-z0-9_.@:-]+)\.(service|socket|timer)', value)
    return (match is not None and match[1] not in (CAPTURE if capture else MANAGEMENT)
            and not value.startswith(tuple(RULES['prefixes'])))
