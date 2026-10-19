"""Test the actual check shell with faithful systemctl is-active semantics."""

import os
from pathlib import Path
import subprocess
import tempfile


def assert_services(testcase, script, services):
    # Isolate service checks; separate tests verify identity and disks.
    setup = """
test() { return 0; }
mountpoint() { return 1; }
cat() { printf 'uuid\\n'; }
hostname() { printf 'guest\\n'; }
findmnt() { printf '/dev/sda2\\n'; }
readlink() { printf '/nix/store/system\\n'; }
lsblk() { printf 'serial\\n'; }
systemctl() {
    if [ "$1" = --failed ]; then return 0; fi
    [ "$1" = is-active ] || return 2
    shift
    for service in "$@"; do
        case "$service" in --*) continue ;; esac
        printf '%s\\n' "$service" >> "$SERVICE_CALLS"
        if [ "$service" != "$INACTIVE_SERVICE" ]; then return 0; fi
    done
    return 3
}
"""
    with tempfile.TemporaryDirectory() as directory:
        calls = Path(directory) / "calls"
        for inactive in ("", *services):
            calls.write_text("")
            result = subprocess.run(["bash", "-c", setup + script], capture_output=True, text=True,
                                    env={**os.environ, "INACTIVE_SERVICE": inactive, "SERVICE_CALLS": str(calls)},
                                    timeout=5)
            with testcase.subTest(inactive=inactive or "none"):
                testcase.assertEqual(result.returncode == 0, not inactive, result.stdout + result.stderr)
                expected = services if not inactive else services[:services.index(inactive) + 1]
                testcase.assertEqual(calls.read_text().splitlines(), expected)
