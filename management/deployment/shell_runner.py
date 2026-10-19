"""Translate a Bash script once before execution; Bash handles individual messages."""

import os
import subprocess
import sys

from messages import shell_script, error


if __name__ == "__main__":
    try:
        result = subprocess.run([os.environ.get("NIXBERRY_BASH", "bash"), "-s", "--", *sys.argv[2:]],
                                input=shell_script(sys.argv[1]), text=True)
        raise SystemExit(result.returncode)
    except (OSError, ValueError, subprocess.SubprocessError) as failure:
        error("{error}", error=failure)
        raise SystemExit(1)
