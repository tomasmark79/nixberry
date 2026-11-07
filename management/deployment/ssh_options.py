"""Encode filesystem paths for OpenSSH's configuration parser."""

import json
import os
import re


def known_hosts_option(path):
    """Keep quotes, whitespace, backslashes and percent tokens literal."""
    value = os.fspath(path).replace("%", "%%")
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", value):
        value = json.dumps(value, ensure_ascii=False)
    return "UserKnownHostsFile=" + value
