"""Test the actual check shell with faithful systemctl is-active semantics."""

import os
from pathlib import Path
import subprocess
import tempfile


def assert_services(testcase, script, services):
    # Isolate service checks; separate tests verify identity and disks.
    setup = r"""
test() { if [ "$1" = -z ]; then builtin test "$@"; else return 0; fi; }
mountpoint() { return 1; }
cat() { printf 'uuid\n'; }
hostname() { printf 'guest\n'; }
findmnt() { printf '/dev/sda2\n'; }
readlink() { printf '/nix/store/system\n'; }
lsblk() { printf 'serial\n'; }
systemctl() {
    if [ "$1" = --failed ]; then
        case "$FAILED_QUERY" in
            error) printf 'query failed\n' >&2; return 1 ;;
            units) printf 'failed.service\n' ;;
        esac
        return 0
    fi
    [ "$1" = is-active ] || return 2
    shift
    for service in "$@"; do
        case "$service" in --*) continue ;; esac
        printf '%s\n' "$service" >> "$SERVICE_CALLS"
        if [ "$service" != "$INACTIVE_SERVICE" ]; then return 0; fi
    done
    return 3
}
"""
    with tempfile.TemporaryDirectory() as directory:
        calls = Path(directory) / "calls"
        cases = [("", ""), *((unit, "") for unit in services), ("", "error"), ("", "units")]
        for inactive, failed_query in cases:
            calls.write_text("")
            result = subprocess.run(["bash", "-c", setup + script], capture_output=True, text=True,
                                    env={**os.environ, "INACTIVE_SERVICE": inactive, "SERVICE_CALLS": str(calls),
                                         "FAILED_QUERY": failed_query},
                                    timeout=5)
            with testcase.subTest(inactive=inactive or "none", failed_query=failed_query):
                testcase.assertEqual(result.returncode == 0, not (inactive or failed_query), result.stdout + result.stderr)
                expected = services if not inactive else services[:services.index(inactive) + 1]
                testcase.assertEqual(calls.read_text().splitlines(), expected)


def assert_boot_partitions(testcase, script, disk, *, uuid="uuid"):
    setup = r"""
test() { if [ "$1" = -d ]; then return 0; else builtin test "$@"; fi; }
mountpoint() { [ "$ISO_MOUNTED" = yes ]; }
uname() { echo x86_64; }
cat() { echo "$EXPECTED_UUID"; }
hostname() { echo guest; }
findmnt() {
    case "$*" in
        *'FSTYPE /boot') echo vfat;;
        *'FSTYPE /') echo ext4;;
        *'SOURCE /boot') echo "/dev/${BOOT_DISK}1";;
        *) echo "/dev/${DISK}2";;
    esac
}
readlink() {
    case "$2" in
        /run/current-system) echo /nix/store/system;;
        */disk-system-root) echo "/dev/${DISK}2";;
        */disk-system-boot) echo "/dev/${DISK}1";;
        *) echo "$2";;
    esac
}
lsblk() {
    case "$*" in *'PKNAME '*) local path="$3"; echo "${path:5:3}";; esac
}
systemctl() { return 0; }
"""
    for boot, iso, allowed in ((disk, "no", True), ("sdb", "no", False), (disk, "yes", False)):
        with testcase.subTest(boot=boot, iso=iso):
            result = subprocess.run(["bash", "-c", setup + script], capture_output=True, text=True,
                                    env={**os.environ, "DISK": disk, "BOOT_DISK": boot, "ISO_MOUNTED": iso,
                                         "EXPECTED_UUID": uuid},
                                    timeout=5)
            testcase.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
