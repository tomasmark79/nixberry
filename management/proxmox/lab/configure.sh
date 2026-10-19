#!/usr/bin/env bash
# Only run through finish.py with the shared message helper included.
set -euo pipefail
fail() { nb_error "$@"; exit 1; }
[[ $# == 1 && $EUID == 0 ]] || fail 'Lab configuration requires root and the expected UUID.'
[[ $(hostname -s) == pvelab && $(cat /sys/class/dmi/id/product_uuid) == "$1" ]] || fail 'The Proxmox lab identity does not match the declaration.'
[[ $(lsblk -dn -o SERIAL /dev/vda) == nixberry-pve-os ]] || fail 'The system disk does not have the expected serial.'
[[ $(lsblk -dn -o SERIAL /dev/vdb) == nixberry-pve-data ]] || fail 'The data disk does not have the expected serial.'
pveversion >/dev/null
install -d -m 755 /etc/default/grub.d
cat > /etc/default/grub.d/99-nixberry-serial.cfg <<'EOF'
# Kernel and GRUB write to the serial port captured by outer libvirt.
GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX:-} console=tty0 console=ttyS0,115200n8"
GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT:-} loglevel=7"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200 --word=8 --parity=no --stop=1"
EOF
nb_info 'Configuring the serial console to capture potential kernel panics…'
update-grub
[[ -c /dev/kvm ]] || fail 'Proxmox has no available KVM device.'
if ! vgs nixberry-lab >/dev/null 2>&1; then
    # Only write to a new blank virtual disk with a verified serial.
    [[ $(lsblk -nr -o NAME /dev/vdb | wc -l) == 1 ]] || fail 'The data disk has partitions or other attached devices.'
    [[ -z $(lsblk -nr -o MOUNTPOINTS /dev/vdb | tr -d '[:space:]') ]] || fail 'The data disk is mounted.'
    if blkid -p /dev/vdb >/dev/null 2>&1; then
        fail 'The data disk contains recognized data; nothing will be erased.'
    fi
    nb_info 'Preparing the blank lab data disk as LVM thin storage…'
    pvcreate /dev/vdb
    vgcreate nixberry-lab /dev/vdb
    lvcreate --type thin-pool -l 95%FREE -n data nixberry-lab
fi
[[ $(pvs --noheadings -o vg_name /dev/vdb | xargs) == nixberry-lab ]] || fail 'The data disk does not belong to the expected LVM group.'
[[ $(vgs --noheadings -o pv_count nixberry-lab | xargs) == 1 ]] || fail 'The LVM group uses another disk.'
lvs nixberry-lab/data >/dev/null
if ! pvesh get /storage/lab-vm --output-format json >/dev/null 2>&1; then
    pvesm add lvmthin lab-vm --vgname nixberry-lab --thinpool data --content images,rootdir
fi
pvesh get /storage/lab-vm --output-format json | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s["type"]=="lvmthin" and s["vgname"]=="nixberry-lab" and s["thinpool"]=="data", s
'
[[ -d /sys/class/net/vmbr0/bridge ]] || fail 'Proxmox does not have the expected vmbr0 bridge.'
install -d -m 700 /var/lib/nixberry
nb_success 'The Proxmox lab has lab-vm storage, vmbr0 bridge and KVM ready.'
printf '{"storage":"lab-vm","bridge":"vmbr0","kvm":true}\n'
