"""Encode filesystem paths for OpenSSH's configuration parser."""

import json
import os
from pathlib import Path
import re
import tempfile


def known_hosts_option(path):
    """Keep quotes, whitespace, backslashes and percent tokens literal."""
    value = os.fspath(path).replace("%", "%%")
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", value):
        value = json.dumps(value, ensure_ascii=False)
    return "UserKnownHostsFile=" + value


def known_hosts(spec):
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    identity = spec["uuid"] if "uuid" in spec else spec["ownerId"]
    return root / "nixberry/portable" / identity / "known_hosts"


def installed_key_options(pin, staged):
    # A present pin is authoritative, even if SSH cannot read it or the IP changed.
    pinned = os.path.lexists(pin)
    options = ["-o", "StrictHostKeyChecking=" + ("yes" if pinned else "accept-new"),
               "-o", known_hosts_option(pin if pinned else staged), "-o", "UpdateHostKeys=no",
               "-o", "ControlPath=none"]
    if pinned:
        options += ["-o", "GlobalKnownHostsFile=/dev/null", "-o", "KnownHostsCommand=none"]
    return options


def remember_installed_key(pin, staged):
    if not staged.exists() and os.path.lexists(pin):
        return  # Verification used the existing pin.
    pin.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=pin.parent) as temporary:
        temporary.write(staged.read_bytes())
        temporary.flush()
        # Publish a verified first key atomically without replacing a concurrent pin.
        os.link(temporary.name, pin)
