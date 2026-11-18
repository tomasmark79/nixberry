"""Shell checks shared by installed system verifiers."""

import shlex


NO_ISO = "if mountpoint -q /iso; then false; fi"
NO_FAILED_UNITS = 'failed_units=$(systemctl --failed --no-legend --plain --no-pager) && test -z "$failed_units"'


def partition_checks(root_label="disk-system-root", boot_label="disk-system-boot"):
    """Require the mounted partitions to match their labels and share a disk."""
    checks = []
    for mount, label in (("/", root_label), ("/boot", boot_label)):
        partition = shlex.quote("/dev/disk/by-partlabel/" + label)
        checks.append('test "$(readlink -f "$(findmnt -n --nofsroot -o SOURCE ' + mount + ')")" = '
                      '"$(readlink -e ' + partition + ')"')
    checks += [
        'test -n "$(lsblk -ndo PKNAME "$(findmnt -n --nofsroot -o SOURCE /)")"',
        'test "$(lsblk -ndo PKNAME "$(findmnt -n --nofsroot -o SOURCE /)")" = '
        '"$(lsblk -ndo PKNAME "$(findmnt -n --nofsroot -o SOURCE /boot)")"',
    ]
    return checks
