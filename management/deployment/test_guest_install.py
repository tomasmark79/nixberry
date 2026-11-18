"""Run each blank-disk installer with simulated hardware and disk probes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


MANAGEMENT = Path(__file__).resolve().parents[1]
SETUP = r'''
nb_error() { printf '%s\n' "$1" >&2; }
nb_info() { :; }
cat() { echo test-uuid; }
uname() { echo "$TEST_ARCH"; }
hostname() { echo nixberry-installer; }
mountpoint() { return 0; }
test() { return 0; }
blockdev() { echo 1024; }
findmnt() { if [[ "$1" == --mountpoint ]]; then return 1; fi; echo tmpfs; }
lsblk() {
    case "$*" in
        *MOUNTPOINTS*) ;;
        *SERIAL*) echo disk-serial ;;
        *) echo "$TEST_DISK" ;;
    esac
}
jq() {
    case "$2" in
        .profile) echo "$TEST_PROFILE" ;;
        .disk) echo "$TEST_DISK" ;;
        .system) echo /nix/store/test-system ;;
        .disko) echo "$TEST_DISKO" ;;
        *) return 0 ;;
    esac
}
wipefs() {
    [[ "$*" == "--no-act --noheadings $TEST_DISK" ]] || return 98
    case "$TEST_SCAN" in
        error) printf '%s\n' 'probe failed' >&2; return 1 ;;
        occupied) echo signature ;;
    esac
}
nix() { return 0; }
'''


class BlankDiskInstallerTests(unittest.TestCase):
    def test_only_successful_empty_signature_scan_can_reach_disko(self):
        variants = (
            ("libvirt", "libvirt", "nixberry-vm", "/dev/vda"),
            ("hyperv", "hyperv", "nixberry-hyperv", "/dev/sda"),
            ("proxmox", "deployment", "nixberry-proxmox", "/dev/vda"),
            ("virtualbox", "deployment", "nixberry-virtualbox", "/dev/sda"),
            ("vmware", "deployment", "nixberry-vmware", "/dev/sda"),
            ("rpi", "rpi", "", ""),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disko = root / "disko"
            disko.write_text("#!/bin/sh\nprintf '%s\\n' 'disko reached'\nexit 77\n")
            disko.chmod(0o700)
            device = root / "device"
            device.touch()
            serial = root / "serial"
            serial.write_bytes(b"test-serial\0")
            for backend, source, profile, disk in variants:
                script = (MANAGEMENT / source / "guest-install.sh").read_text()
                architecture = "x86_64"
                system = "/nix/store/test-system"
                arguments = ["apply", "test-uuid", system, str(disko), "1024"]
                if source == "deployment":
                    arguments += [profile, disk]
                if source == "rpi":
                    # Substitute fixture paths for the hardware serial and block node.
                    script = script.replace("/proc/device-tree/serial-number", '"$TEST_SERIAL"')
                    script = script.replace('[[ -b "$device" ]]', '[[ -f "$device" ]]')
                    disk, architecture = str(device), "aarch64"
                    arguments = ["test-serial", disk, "disk-serial", "1024", system, str(disko),
                                 "nixberry-ci-boot", "nixberry-ci-root", "jq", "clean-only"]
                for scan, status in (("error", 1), ("occupied", 1), ("blank", 77)):
                    with self.subTest(backend=backend, scan=scan):
                        result = subprocess.run(
                            ["bash", "-c", SETUP + script, "installer", *arguments],
                            env={**os.environ, "TEST_SCAN": scan, "TEST_PROFILE": profile, "TEST_DISK": disk,
                                 "TEST_ARCH": architecture, "TEST_DISKO": str(disko), "TEST_SERIAL": str(serial)},
                            capture_output=True, text=True, timeout=5,
                        )
                        self.assertEqual(result.returncode, status, result.stderr)
                        self.assertEqual("disko reached" in result.stdout, scan == "blank")
                        if scan == "error":
                            self.assertIn("probe failed", result.stderr)
                            self.assertIn("Cannot verify disk signatures.", result.stderr)
